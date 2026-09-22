"""Rolling-horizon optimizer (model predictive control with CP-SAT).

At a replanning point the planner solves one scheduling problem for the next `horizon`
steps with all constraints at once and executes it step by step. It replans every
`replan_every` steps, after any received event or goal change, and immediately if a
planned action turns out to be inadmissible.

Model over steps t in [k, k + H), satellites s:
- x[j,s,t]  satellite s works on job j at step t (eligible, contact, available, window);
- c[s,t]    calibration;
- E[s,t]    energy in 0.01 Wh units, a guaranteed LOWER bound of the real charge
            (may go below zero where the real charge would clamp at zero).
Constraints:
- one action per satellite and step, one executor per job and step;
- at most `downlink_parallel_limit` downlinks per step;
- a job action needs a valid calibration: initial age still valid or a calibration in
  the last `calibration_valid_steps` steps;
- energy: E[t+1] <= E[t] + min(eta_c * b, b / eta_d) with b = solar - load(action),
  E <= capacity; the positive branch is dropped when charging is not allowed. The
  real transition clamps and never wastes energy, so real charge >= E (induction);
  coefficients are rounded conservatively. Heater and charging windows come from an
  idle temperature forecast, which is the coldest possible trajectory;
- acting at t needs E[t] >= reserve and E[t+1] >= reserve (the model's admission rule);
- a job whose deadline is inside the horizon is either completed or not touched
  (sum of work = remaining * z_j), so no work is wasted on jobs that will miss.
Objective (lexicographic through weights):
- goal "priority": priority-3 completions >> revenue;
- goal "revenue": revenue >> priority-3 completions;
then partial progress on jobs that end beyond the horizon, energy left at the horizon
end, earlier execution (keeps slack for urgent requests), fewer calibrations.

Deterministic: one worker, fixed seed, deterministic time limit.
"""
from __future__ import annotations

import math

from ortools.sat.python import cp_model

from .optimizer_common import UNITS_PER_WH as MWH
from .optimizer_common import step_terms
from .optimizer_common import idle_temperature as _idle_temperature
from .optimizer_common import unavailable as _unavailable
from .reservation import ReservationPlanner, validated



class OptimizerPlanner:
    name = "cpsat-mpc"
    version = "1.0.0"

    def __init__(self, horizon: int = 36, replan_every: int = 12, time_limit: float = 3.0,
                 terminal_soc_pct: float = 55.0, partial_credit: float = 0.8, seed: int = 0,
                 energy_margin_wh: float = 0.3, max_candidates: int = 0, max_variables: int = 15000):
        if horizon < 2 or replan_every < 1 or replan_every > horizon:
            raise ValueError("Need 2 <= horizon and 1 <= replan_every <= horizon")
        if time_limit <= 0:
            raise ValueError("time_limit must be positive")
        self.horizon = int(horizon)
        self.replan_every = int(replan_every)
        self.time_limit = float(time_limit)
        self.terminal_soc_pct = float(terminal_soc_pct)
        self.partial_credit = float(partial_credit)
        self.seed = int(seed)
        self.energy_margin_wh = float(energy_margin_wh)
        if max_candidates < 0:
            raise ValueError("max_candidates must be >= 0 (0 = keep all)")
        self.max_candidates = int(max_candidates)
        # Above this model size the exact solve does not fit the budget on one CPU:
        # the reservation plan is used directly (reported as such in the planner log).
        self.max_variables = int(max_variables)
        # Internal state; plain data so that fork() can deep-copy it.
        self._plan: dict[int, dict[str, dict]] = {}
        self._planned_at = -1
        self._signature = None
        self.solves: list[dict] = []

    def params(self) -> dict:
        return {"horizon": self.horizon, "replan_every": self.replan_every,
                "time_limit": self.time_limit, "terminal_soc_pct": self.terminal_soc_pct,
                "partial_credit": self.partial_credit, "seed": self.seed,
                "energy_margin_wh": self.energy_margin_wh, "max_candidates": self.max_candidates,
                "max_variables": self.max_variables}

    def restore(self, session) -> None:
        self._plan, self._planned_at, self._signature = {}, -1, None

    def report(self) -> dict:
        """Replanning log for the operator: why and how well each plan was made."""
        solves = self.solves
        proven = sum(x["status"] == "OPTIMAL" or x.get("gap") == 0 for x in solves)
        return {"replans": len(solves), "proven_optimal": proven,
                "by_reason": {r: sum(x["reason"] == r for x in solves) for r in {x["reason"] for x in solves}},
                "fallbacks": sum(x["status"] not in ("OPTIMAL", "FEASIBLE") for x in solves),
                "solves": solves[-100:]}

    # ------------------------------------------------------------------ execution

    def plan(self, session, goal: str) -> dict[str, dict]:
        env = session.env
        k = env.k
        signature = (goal, len(session.events), len(env.jobs))
        if (signature != self._signature or k not in self._plan
                or k - self._planned_at >= self.replan_every):
            self._solve(env, goal, reason=self._reason(signature, k))
            self._signature = signature
        actions, ok = self._validated(env, self._plan.get(k, {}))
        if not ok:  # reality differs from the plan: replan right now
            self._solve(env, goal, reason="deviation")
            actions, _ = self._validated(env, self._plan.get(k, {}))
        return actions

    def _reason(self, signature, k) -> str:
        if self._signature is None:
            return "start"
        if signature[0] != self._signature[0]:
            return "goal_changed"
        if signature[1:] != self._signature[1:]:
            return "event"
        return "periodic"

    @staticmethod
    def _validated(env, planned):
        return validated(env, planned)

    # ------------------------------------------------------------------ model

    def _prune(self, candidates, goal: str, k: int):
        """Keep at most `max_candidates` jobs per satellite-step, ranked by value per remaining
        step (priority first for the priority goal). Jobs already started or in the current
        plan are always kept, so pruning never abandons work in progress."""
        keep_always = {a["job_id"] for t, acts in self._plan.items() if t >= k
                       for a in acts.values() if a["action"] == "job"}

        def rank(j):
            density = j["value_usd"] / j["remaining_steps"]
            crit = j["priority"] == 3
            return (crit, density) if goal == "priority" else (density, crit)

        per_cell: dict[tuple[str, int], list] = {}
        for idx, (j, cells) in enumerate(candidates):
            for cell in cells:
                per_cell.setdefault(cell, []).append(idx)
        allowed: set[tuple[int, tuple[str, int]]] = set()
        for cell, idxs in per_cell.items():
            idxs.sort(key=lambda i: rank(candidates[i][0]), reverse=True)
            for i in idxs[: self.max_candidates]:
                allowed.add((i, cell))
        out = []
        for idx, (j, cells) in enumerate(candidates):
            if j["id"] in keep_always or j["remaining_steps"] < j["work_steps"]:
                out.append((j, cells))
            else:
                out.append((j, [c for c in cells if (idx, c) in allowed]))
        return out

    def _solve(self, env, goal: str, reason: str) -> None:
        k, n = env.k, env.s["time"]["steps"]
        H = min(self.horizon, n - k)
        steps = range(k, k + H)
        m, sats = env.s["model"], sorted(env.sats)
        dt = env.s["time"]["step_s"]
        eta_c, eta_d = m["charge_efficiency"], m["discharge_efficiency"]
        k_pos = eta_c * dt / 3600 * MWH  # mWh per W of surplus
        k_neg = dt / 3600 / eta_d * MWH  # mWh per W of deficit
        valid = m["calibration_valid_steps"]

        avail = {s: [not _unavailable(env, s, t) for t in steps] for s in sats}
        temp, heater = {}, {}
        for s in sats:
            temp[s], heater[s] = _idle_temperature(env, s, k, H)

        def payload_ok(s, i):
            tt = temp[s][i]
            return m["payload_min_c"] + 0.5 <= tt <= m["payload_max_c"] - 0.5

        mdl = cp_model.CpModel()
        x: dict[tuple[str, str, int], cp_model.IntVar] = {}
        by_st: dict[tuple[str, int], list] = {}
        power_st: dict[tuple[str, int], list] = {}
        by_jt: dict[tuple[str, int], list] = {}
        dl_t: dict[int, list] = {}
        job_vars: dict[str, list] = {}
        complete, partial = {}, {}

        candidates = []
        for j in env.jobs.values():
            rem = j["remaining_steps"]
            if j["completed_step"] is not None or rem <= 0:
                continue
            lo, hi = max(j["release_step"], k), min(j["deadline_step"], k + H)
            if lo >= hi:
                continue
            key = j["kind"] + "_available"
            cells = [(s, t) for t in range(lo, hi) for s in j["eligible_satellites"]
                     if env.s["environment"][s][key][t] and avail[s][t - k] and payload_ok(s, t - k)]
            candidates.append((j, cells))
        if self.max_candidates:
            candidates = self._prune(candidates, goal, k)

        for j, cells in candidates:
            rem = j["remaining_steps"]
            inside = j["deadline_step"] <= k + H
            slots = len({t for _, t in cells})
            if not cells or (inside and slots < rem):
                continue  # cannot finish inside the horizon: leave it untouched
            vs = []
            for s, t in cells:
                v = mdl.NewBoolVar("")
                x[(j["id"], s, t)] = v
                vs.append(v)
                by_st.setdefault((s, t), []).append(v)
                power_st.setdefault((s, t), []).append((v, env.sats[s][j["kind"] + "_w"]))
                by_jt.setdefault((j["id"], t), []).append(v)
                if j["kind"] == "downlink":
                    dl_t.setdefault(t, []).append(v)
            job_vars[j["id"]] = vs
            if inside:
                z = mdl.NewBoolVar("")
                mdl.Add(sum(vs) == rem * z)
                complete[j["id"]] = z
            else:
                mdl.Add(sum(vs) <= rem)
                partial[j["id"]] = vs

        # Calibration variables only where they can matter: a satellite whose calibration
        # stays valid for the whole horizon never needs one, and while the horizon is not
        # longer than the validity period one calibration covers the rest of it.
        cal = {}
        for s in sats:
            if env.state[s]["calibration_age_steps"] + H - 1 < valid:
                continue
            mine = []
            for i, t in enumerate(steps):
                if avail[s][i] and payload_ok(s, i):
                    c = mdl.NewBoolVar("")
                    cal[(s, t)] = c
                    mine.append(c)
                    by_st.setdefault((s, t), []).append(c)
                    power_st.setdefault((s, t), []).append((c, env.sats[s]["calibration_w"]))
            if H <= valid and len(mine) > 1:
                mdl.AddAtMostOne(mine)

        for vs in by_st.values():
            if len(vs) > 1:
                mdl.AddAtMostOne(vs)
        for vs in by_jt.values():
            if len(vs) > 1:
                mdl.AddAtMostOne(vs)
        for vs in dl_t.values():
            if len(vs) > m["downlink_parallel_limit"]:
                mdl.Add(sum(vs) <= m["downlink_parallel_limit"])

        # Calibration validity for job actions.
        job_st: dict[tuple[str, int], list] = {}
        for (jid, s, t), v in x.items():
            job_st.setdefault((s, t), []).append(v)
        for (s, t), vs in job_st.items():
            age0 = env.state[s]["calibration_age_steps"] + (t - k)
            if age0 < valid:
                continue
            recent = [cal[(s, u)] for u in range(max(k, t - valid), t) if (s, u) in cal]
            if not recent:
                for v in vs:
                    mdl.Add(v == 0)
            else:
                mdl.Add(sum(vs) <= sum(recent))

        # Energy lower bound per satellite.
        terminal_short = []
        for s in sats:
            v = env.sats[s]
            cap = int(math.floor(v["capacity_wh"] * MWH))
            reserve = int(math.ceil((v["capacity_wh"] * m["reserve_soc_pct"] / 100 + self.energy_margin_wh) * MWH))
            e0 = int(math.floor(env.state[s]["energy_wh"] * MWH))
            # Lower bound may go negative where the real charge clamps at zero.
            E = [mdl.NewConstant(min(e0, cap))] + [mdl.NewIntVar(-cap * H, cap, "") for _ in range(H)]
            solar = env.s["environment"][s]["solar_w"]
            for i, t in enumerate(steps):
                b0 = solar[t] - v["base_w"] - heater[s][i]
                acts = power_st.get((s, t), [])
                charge = m["charge_min_c"] + 0.5 <= temp[s][i] <= m["charge_max_c"] - 0.5
                neg0, neg_p = step_terms(k_neg, b0, [p for _, p in acts])
                mdl.Add(E[i + 1] <= E[i] + neg0 - sum(c * a for c, (a, _) in zip(neg_p, acts)))
                if charge:
                    pos0, pos_p = step_terms(k_pos, b0, [p for _, p in acts])
                    mdl.Add(E[i + 1] <= E[i] + pos0 - sum(c * a for c, (a, _) in zip(pos_p, acts)))
                else:
                    mdl.Add(E[i + 1] <= E[i])
                if acts:
                    act = sum(a for a, _ in acts)
                    mdl.Add(E[i] >= reserve * act)
                    mdl.Add(E[i + 1] >= reserve * act)
            target = min(e0, int(v["capacity_wh"] * self.terminal_soc_pct / 100 * MWH))
            short = mdl.NewIntVar(0, cap * (H + 1), "")
            mdl.Add(short >= target - E[H])
            terminal_short.append(short)

        # Objective.
        cents = {jid: int(round(env.jobs[jid]["value_usd"] * 100)) for jid in job_vars}
        total_cents = sum(cents.values()) + 1
        n_crit = sum(env.jobs[j]["priority"] == 3 for j in job_vars) + 1
        SCALE = 20  # keeps job weights well above the energy and timing terms

        def weight(jid: str) -> int:
            crit = env.jobs[jid]["priority"] == 3
            if goal == "priority":
                return SCALE * ((total_cents if crit else 0) + cents[jid] + 1)
            return SCALE * (cents[jid] * n_crit + (1 if crit else 0) + 1)

        obj = []
        for jid, z in complete.items():
            obj.append(weight(jid) * z)
        for jid, vs in partial.items():
            per_step = int(weight(jid) * self.partial_credit / env.jobs[jid]["remaining_steps"])
            obj.extend(per_step * v for v in vs)
        # small terms: early work, few calibrations, energy at the horizon end
        obj.extend(-(t - k) * v for (_, _, t), v in x.items())
        obj.extend(-5 * c for c in cal.values())
        obj.extend(-1 * s for s in terminal_short)
        mdl.Maximize(sum(obj))

        # Warm start: a complete assignment from the reservation heuristic over the same
        # horizon. The solver starts from it and can only improve; if it finds nothing in
        # the budget, the reservation plan itself is used.
        seed = ReservationPlanner(energy_margin_wh=self.energy_margin_wh).replan(env, goal, horizon=H)
        seeded = {(a["job_id"], s, t) for t, acts in seed.items() for s, a in acts.items() if a["action"] == "job"}
        seeded_cal = {(s, t) for t, acts in seed.items() for s, a in acts.items() if a["action"] == "calibrate"}
        for key, v in x.items():
            mdl.AddHint(v, key in seeded)
        for key, c in cal.items():
            mdl.AddHint(c, key in seeded_cal)
        done_in_seed = {}
        for jid, _, _ in seeded:
            done_in_seed[jid] = done_in_seed.get(jid, 0) + 1
        for jid, z in complete.items():
            mdl.AddHint(z, done_in_seed.get(jid, 0) == env.jobs[jid]["remaining_steps"])

        if len(x) + len(cal) > self.max_variables:
            # Too large for an exact solve: plan by reservation over the whole remaining
            # shift, which packs better than a horizon-limited reservation.
            seed = ReservationPlanner(energy_margin_wh=self.energy_margin_wh).replan(env, goal)
            self._plan = dict(seed)
            for t in steps:
                self._plan.setdefault(t, {})
            self._planned_at = k
            self.solves.append({"step": k, "reason": reason, "horizon": H, "variables": len(x) + len(cal),
                                "jobs_considered": len(job_vars), "status": "RESERVATION", "wall_s": 0.0,
                                "note": "model too large for the exact solve budget",
                                "planned_completions": sum(1 for jid, n_ in done_in_seed.items()
                                                           if n_ == env.jobs[jid]["remaining_steps"])})
            return

        solver = cp_model.CpSolver()
        solver.parameters.num_workers = 1
        # Full LP relaxation: proves optimality of these horizon problems in seconds.
        solver.parameters.linearization_level = 2
        solver.parameters.random_seed = self.seed
        solver.parameters.max_deterministic_time = self.time_limit
        # Stop once the proven gap is below one dollar: no whole job can hide in it.
        solver.parameters.absolute_gap_limit = SCALE * 100
        solver.parameters.repair_hint = True
        status = solver.Solve(mdl)

        stats = {"step": k, "reason": reason, "horizon": H, "variables": len(x) + len(cal),
                 "jobs_considered": len(job_vars), "status": solver.StatusName(status),
                 "wall_s": round(solver.WallTime(), 3)}
        if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            plan: dict[int, dict[str, dict]] = {}
            for (jid, s, t), v in x.items():
                if solver.Value(v):
                    plan.setdefault(t, {})[s] = {"action": "job", "job_id": jid}
            for (s, t), c in cal.items():
                if solver.Value(c):
                    plan.setdefault(t, {})[s] = {"action": "calibrate"}
            stats.update(objective=solver.ObjectiveValue(), bound=solver.BestObjectiveBound(),
                         planned_completions=sum(solver.Value(z) for z in complete.values()))
            gap = abs(stats["bound"] - stats["objective"]) / max(1.0, abs(stats["bound"]))
            stats["gap"] = round(gap, 6)
            # Safety net: execute whichever plan completes more by the goal's own ranking,
            # so a time-limited solve can never do worse than the reservation seed.
            if _completion_value(env, seed, weight, k + H) > _completion_value(env, plan, weight, k + H):
                plan = dict(seed)
                stats["status"] = "RESERVATION"
                stats["note"] = "reservation plan ranked higher than the time-limited solve"
            self._plan = {t: a for t, a in plan.items()}
            for t in steps:
                self._plan.setdefault(t, {})
        else:
            # No solution in the budget: execute the reservation plan.
            stats["status"] = "RESERVATION"
            stats["planned_completions"] = sum(1 for jid, n_ in done_in_seed.items()
                                               if n_ == env.jobs[jid]["remaining_steps"])
            self._plan = dict(seed)
            for t in steps:
                self._plan.setdefault(t, {})
        self._planned_at = k
        self.solves.append(stats)
        if len(self.solves) > 400:
            self.solves = self.solves[-400:]



def _completion_value(env, plan: dict, weight, end: int) -> int:
    """Goal-weighted value of the jobs a plan completes by `end` (their remaining work fully planned)."""
    planned: dict[str, int] = {}
    for t, acts in plan.items():
        if t < end:
            for a in acts.values():
                if a["action"] == "job":
                    planned[a["job_id"]] = planned.get(a["job_id"], 0) + 1
    total = 0
    for jid, n in planned.items():
        j = env.jobs[jid]
        if n >= j["remaining_steps"]:
            try:
                total += weight(jid)
            except KeyError:  # job not in the exact model (e.g. beyond the horizon)
                continue
    return total
