"""Reservation planner: all-or-nothing packing of jobs into satellite-steps.

At a replanning point every open job is considered once, in goal order, and is admitted
only if ALL its remaining work can be reserved inside its window: free eligible
satellite-steps with contact, the downlink limit, a valid calibration, and a charge that
stays above the reserve under a conservative energy model. A job that does not fit is not
started, so no work is wasted on jobs that would miss their deadline.

Order:
- jobs already in progress first (their work is sunk);
- goal "priority": priority 3 first, then value per remaining step;
- goal "revenue": value per remaining step, then priority 3;
- ties: fewer options first (most constrained), earlier deadline.
Cells are taken where competition is lowest (expected demand of the other jobs), so
flexible jobs leave scarce contacts to the jobs that need them.

Calibrations are reserved just before the current one expires, at the least demanded step.
The plan is re-made every `replan_every` steps, after any event or goal change, and
immediately if an action turns out to be inadmissible. It runs in about a second even for
the 8120-job scenario, and serves as the warm start for the CP-SAT optimizer.
"""
from __future__ import annotations

import math

from .optimizer_common import EnergyModel, idle_temperature, unavailable


class ReservationPlanner:
    name = "reserve"
    version = "1.0.0"

    def __init__(self, replan_every: int = 12, energy_margin_wh: float = 0.3, calibration_lead: int = 8):
        if replan_every < 1 or calibration_lead < 1:
            raise ValueError("replan_every and calibration_lead must be >= 1")
        self.replan_every = int(replan_every)
        self.energy_margin_wh = float(energy_margin_wh)
        self.calibration_lead = int(calibration_lead)
        self._plan: dict[int, dict[str, dict]] = {}
        self._planned_at = -1
        self._signature = None
        self.solves: list[dict] = []

    def params(self) -> dict:
        return {"replan_every": self.replan_every, "energy_margin_wh": self.energy_margin_wh,
                "calibration_lead": self.calibration_lead}

    def restore(self, session) -> None:
        self._plan, self._planned_at, self._signature = {}, -1, None

    def report(self) -> dict:
        return {"replans": len(self.solves), "proven_optimal": 0, "fallbacks": 0,
                "by_reason": {r: sum(x["reason"] == r for x in self.solves) for r in {x["reason"] for x in self.solves}},
                "solves": self.solves[-100:]}

    # ------------------------------------------------------------------ execution

    def plan(self, session, goal: str) -> dict[str, dict]:
        env = session.env
        k = env.k
        signature = (goal, len(session.events), len(env.jobs))
        if signature != self._signature or k - self._planned_at >= self.replan_every or k not in self._plan:
            reason = "start" if self._signature is None else (
                "goal_changed" if signature[0] != self._signature[0] else
                "event" if signature[1:] != self._signature[1:] else "periodic")
            self.replan(env, goal, reason)
            self._signature = signature
        actions, ok = validated(env, self._plan.get(k, {}))
        if not ok:
            self.replan(env, goal, "deviation")
            actions, _ = validated(env, self._plan.get(k, {}))
        return actions

    # ------------------------------------------------------------------ packing

    def replan(self, env, goal: str, reason: str = "", horizon: int | None = None) -> dict[int, dict[str, dict]]:
        k, n = env.k, env.s["time"]["steps"]
        end = n if horizon is None else min(n, k + horizon)
        m = env.s["model"]
        sats = sorted(env.sats)
        limit = m["downlink_parallel_limit"]
        valid = m["calibration_valid_steps"]

        avail = {s: [not unavailable(env, s, t) for t in range(k, end)] for s in sats}
        energy = {s: EnergyModel(env, s, k, end, self.energy_margin_wh) for s in sats}
        busy = {s: [False] * (end - k) for s in sats}
        dl_used = [0] * (end - k)
        plan: dict[int, dict[str, dict]] = {}

        # Candidate cells per job and the expected demand on each satellite-step.
        cands = []
        demand = {s: [0.0] * (end - k) for s in sats}
        for j in env.jobs.values():
            if j["completed_step"] is not None or j["remaining_steps"] <= 0:
                continue
            lo, hi = max(j["release_step"], k), min(j["deadline_step"], end)
            if hi - lo < j["remaining_steps"]:
                continue
            key = j["kind"] + "_available"
            cells = [(s, t) for t in range(lo, hi) for s in j["eligible_satellites"]
                     if env.s["environment"][s][key][t] and avail[s][t - k]]
            steps_ok = {t for _, t in cells}
            if len(steps_ok) < j["remaining_steps"]:
                continue
            cands.append((j, cells))
            share = j["remaining_steps"] / len(cells)
            for s, t in cells:
                demand[s][t - k] += share

        # Calibrations: one just before each expiry, at the least demanded step.
        for s in sats:
            age = env.state[s]["calibration_age_steps"]
            expiry = k + max(0, valid - age)  # first step that would need a calibration
            while expiry < end:
                window = [t for t in range(max(k, expiry - self.calibration_lead), expiry + 1)
                          if t < end and avail[s][t - k] and not busy[s][t - k]]
                window.sort(key=lambda t: (demand[s][t - k], -t))
                placed = None
                for t in window:
                    if energy[s].try_add(t, env.sats[s]["calibration_w"]):
                        placed = t
                        break
                if placed is None:
                    break  # cannot calibrate in time: jobs after expiry stay unplanned
                busy[s][placed - k] = True
                plan.setdefault(placed, {})[s] = {"action": "calibrate"}
                expiry = placed + 1 + valid

        def calibrated(s, t):
            age0 = env.state[s]["calibration_age_steps"] + (t - k)
            if age0 < valid:
                return True
            return any(a.get("action") == "calibrate" and u >= t - valid
                       for u in range(max(k, t - valid), t) for a in [plan.get(u, {}).get(s, {})])

        def rank(item):
            j, cells = item
            started = j["remaining_steps"] < j["work_steps"]
            density = j["value_usd"] / j["remaining_steps"]
            crit = j["priority"] == 3
            options = len(cells) / j["remaining_steps"]
            main = (crit, density) if goal == "priority" else (density, crit)
            return (not started, tuple(-x for x in main), options, j["deadline_step"], j["id"])

        admitted, work = 0, 0
        for j, cells in sorted(cands, key=rank):
            rem = j["remaining_steps"]
            power = {s: env.sats[s][j["kind"] + "_w"] for s in j["eligible_satellites"]}
            by_step: dict[int, list[str]] = {}
            for s, t in cells:
                if not busy[s][t - k] and (j["kind"] != "downlink" or dl_used[t - k] < limit) and calibrated(s, t):
                    by_step.setdefault(t, []).append(s)
            if len(by_step) < rem:
                continue
            order = sorted(by_step, key=lambda t: (min(demand[s][t - k] for s in by_step[t]), t))
            chosen = []
            for t in order:
                for s in sorted(by_step[t], key=lambda s: demand[s][t - k]):
                    if energy[s].try_add(t, power[s]):
                        chosen.append((s, t))
                        break
                if len(chosen) == rem:
                    break
            if len(chosen) < rem:
                for s, t in chosen:
                    energy[s].remove(t, power[s])
                continue
            for s, t in chosen:
                busy[s][t - k] = True
                if j["kind"] == "downlink":
                    dl_used[t - k] += 1
                plan.setdefault(t, {})[s] = {"action": "job", "job_id": j["id"]}
            share = rem / len(cells)
            for s, t in cells:
                demand[s][t - k] -= share
            admitted += 1
            work += rem

        self._plan = plan
        self._planned_at = k
        self.solves.append({"step": k, "reason": reason, "status": "HEURISTIC", "horizon": end - k,
                            "jobs_considered": len(cands), "planned_completions": admitted,
                            "variables": 0, "wall_s": 0.0})
        if len(self.solves) > 400:
            self.solves = self.solves[-400:]
        return plan


def validated(env, planned: dict[str, dict]) -> tuple[dict[str, dict], bool]:
    """Keep only actions the model admits now and that respect the shared limits."""
    out, ok, downlinks, jobs = {}, True, 0, set()
    limit = env.s["model"]["downlink_parallel_limit"]
    for sid in sorted(planned):
        a = planned[sid]
        good = env.can_execute(sid, a)[0]
        if good and a["action"] == "job":
            j = env.jobs[a["job_id"]]
            if a["job_id"] in jobs or (j["kind"] == "downlink" and downlinks >= limit):
                good = False
            else:
                jobs.add(a["job_id"])
                downlinks += j["kind"] == "downlink"
        if good:
            out[sid] = a
        else:
            ok = False
    return out, ok
