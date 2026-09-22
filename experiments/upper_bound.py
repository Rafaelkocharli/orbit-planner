"""Upper bounds for a whole shift: no planner can do better than these numbers.

The bound is an LP relaxation of the real problem. Relaxing (dropping or loosening)
constraints can only raise the optimum, so the values stay valid upper bounds:
- a satellite does at most one job per step, a job has at most one executor per step;
- a job may use only steps in its window where an eligible satellite has contact and is
  available; at most `downlink_parallel_limit` downlinks per step;
- a job counts as done in proportion to its work, and only up to 1;
- calibration is dropped;
- energy (unless --no-energy) in an optimistic form: E' <= min(C, E + g(b)) with
  g(b) = min(eta_c * b, b / eta_d) * dt, heater assumed off, charging always allowed,
  and acting needs E >= reserve at both ends of the step. The real transition satisfies
  this for every run without a brownout (clamping at zero is not modelled).

    python -m experiments.upper_bound                       # all scenarios
    python -m experiments.upper_bound --scenarios P03_energy

Bounds: maximum completed jobs, maximum priority-3 jobs, maximum revenue, and the
maximum revenue given the maximum number of priority-3 jobs (the "priority" goal).
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from ortools.linear_solver import pywraplp

from model.resource_env import load

from backend.app.config import DATA_DIR, ROOT


def build(s: dict, energy: bool = True):
    n = s["time"]["steps"]
    m = s["model"]
    env = s["environment"]
    sats = {v["id"]: v for v in s["satellites"]}

    def available(sid, t):
        return not any(f["satellite_id"] == sid and f["start_step"] <= t < f["end_step"] for f in s["failures"])

    lp = pywraplp.Solver.CreateSolver("GLOP")
    z, by_st, by_jt, dl_t, cell_power = {}, {}, {}, {}, {}
    for j in s["jobs"]:
        key = j["kind"] + "_available"
        cells = [(sid, t) for t in range(j["release_step"], min(j["deadline_step"], n))
                 for sid in j["eligible_satellites"] if env[sid][key][t] and available(sid, t)]
        if len({t for _, t in cells}) < j["work_steps"]:
            continue  # provably infeasible: contributes nothing
        vs = []
        for sid, t in cells:
            v = lp.NumVar(0, 1, "")
            vs.append(v)
            by_st.setdefault((sid, t), []).append(v)
            by_jt.setdefault((j["id"], t), []).append(v)
            cell_power.setdefault((sid, t), []).append((v, sats[sid][j["kind"] + "_w"]))
            if j["kind"] == "downlink":
                dl_t.setdefault(t, []).append(v)
        zj = lp.NumVar(0, 1, "")
        lp.Add(j["work_steps"] * zj <= lp.Sum(vs))
        z[j["id"]] = (zj, j)

    for vs in list(by_st.values()) + list(by_jt.values()):
        if len(vs) > 1:
            lp.Add(lp.Sum(vs) <= 1)
    for vs in dl_t.values():
        if len(vs) > m["downlink_parallel_limit"]:
            lp.Add(lp.Sum(vs) <= m["downlink_parallel_limit"])

    if energy:
        dt = s["time"]["step_s"]
        k_pos = m["charge_efficiency"] * dt / 3600
        k_neg = dt / 3600 / m["discharge_efficiency"]
        for sid, v in sats.items():
            cap = v["capacity_wh"]
            reserve = cap * m["reserve_soc_pct"] / 100
            e0 = cap * v["initial_soc_pct"] / 100
            E = [None] + [lp.NumVar(-lp.infinity(), cap, "") for _ in range(n)]

            def level(t):
                return e0 if t == 0 else E[t]

            for t in range(n):
                acts = cell_power.get((sid, t), [])
                b = env[sid]["solar_w"][t] - v["base_w"] - lp.Sum(p * x for x, p in acts)
                lp.Add(E[t + 1] <= level(t) + k_pos * b)
                lp.Add(E[t + 1] <= level(t) + k_neg * b)
                if acts:
                    act = lp.Sum(x for x, _ in acts)
                    lp.Add(level(t) >= reserve * act)
                    lp.Add(E[t + 1] >= reserve * act)
    return lp, z


def maximize(lp, z, weight):
    lp.Maximize(lp.Sum(weight(j) * v for v, j in z.values()))
    status = lp.Solve()
    if status != pywraplp.Solver.OPTIMAL:
        raise RuntimeError("LP not solved to optimality")
    return lp.Objective().Value()


def bounds(s: dict, energy: bool = True) -> dict:
    lp, z = build(s, energy)
    out = {"energy": energy, "jobs_total": len(s["jobs"]), "feasible_jobs": len(z),
           "critical_total": sum(j["priority"] == 3 for j in s["jobs"]),
           "critical_feasible": sum(j["priority"] == 3 for _, j in z.values())}
    out["max_jobs"] = math.floor(maximize(lp, z, lambda j: 1) + 1e-6)
    out["max_revenue_usd"] = round(maximize(lp, z, lambda j: j["value_usd"]), 2)
    crit = maximize(lp, z, lambda j: 1 if j["priority"] == 3 else 0)
    out["max_critical"] = math.floor(crit + 1e-6)
    # priority goal: keep the priority-3 optimum, then maximize revenue
    lp.Add(lp.Sum(v for v, j in z.values() if j["priority"] == 3) >= crit - 1e-6)
    out["max_revenue_at_max_critical_usd"] = round(maximize(lp, z, lambda j: j["value_usd"]), 2)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scenarios", nargs="*", default=sorted(p.stem for p in DATA_DIR.glob("*.json")))
    ap.add_argument("--no-energy", action="store_true", help="drop the energy relaxation (looser bound)")
    ap.add_argument("--out", default=str(ROOT / "results" / "upper_bounds.json"))
    args = ap.parse_args()
    result = {}
    for sid in args.scenarios:
        result[sid] = bounds(load(DATA_DIR / f"{sid}.json"), energy=not args.no_energy)
        print(sid, json.dumps(result[sid], ensure_ascii=False))
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
