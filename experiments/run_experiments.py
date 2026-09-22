"""Reproducible experiments (T4): every planner x scenario x goal, with and without events.

    python -m experiments.run_experiments                 # all, results in results/
    python -m experiments.run_experiments --scenarios P02_shift --planners greedy-edf
    python -m experiments.run_experiments --examples      # curated runs into examples/runs/

Each run is saved as a cosmo-B-ops-result-1.0 file (replayable with model/operations.py),
plus results/summary.csv and results/summary.md with the key metrics and miss causes.
Planners must be deterministic; a seed, if any, goes into planner params and run_metadata.
"""
from __future__ import annotations

import argparse
import csv
import json
import platform
import time
from collections import defaultdict
from pathlib import Path

from model.operations import Session
from model.resource_env import load

from backend.app.analysis import missed_jobs
from experiments.upper_bound import bounds as upper_bounds
from backend.app.config import ALGORITHM_VERSION, DATA_DIR, EXAMPLES_DIR, ROOT
from backend.app.planners import GOALS, PLANNERS, make_planner

EVENTS_FILE = EXAMPLES_DIR / "events_demo.json"
EXAMPLES = [("P01_intro", "priority", False), ("P02_shift", "priority", True), ("P02_shift", "revenue", True)]


def run_one(scenario: dict, planner_name: str, goal: str, events: list[dict], params: dict | None = None):
    planner = make_planner(planner_name, params)
    session = Session(scenario, run_metadata={
        "goal": goal, "algorithm": planner.name, "version": f"{ALGORITHM_VERSION}/{planner.version}",
        "parameters": planner.params(), "goal_switches": [],
    })
    by_step = defaultdict(list)
    for e in events:
        by_step[e["at_step"]].append(e)
    t0 = time.perf_counter()
    while session.env.k < scenario["time"]["steps"]:
        for e in by_step.get(session.env.k, []):
            session.apply_event(e)
        session.advance(planner.plan(session, goal))
    return session, time.perf_counter() - t0


def row_for(name, scenario_id, planner, goal, with_events, session, seconds, bound=None):
    s = session.summary()
    causes = {c["cause"]: c["jobs"] for c in missed_jobs(session)["by_cause"]}
    due = s["critical_jobs_due"]
    return {
        "name": name, "scenario": scenario_id, "planner": planner, "goal": goal, "events": with_events,
        "jobs_total": s["jobs_total"], "jobs_completed": s["jobs_completed"],
        "jobs_due_missed": s["jobs_due_missed"],
        "critical_on_time": s["critical_jobs_completed_on_time"], "critical_due": due,
        "critical_rate": round(s["critical_jobs_completed_on_time"] / due, 4) if due else None,
        "revenue_usd": s["revenue_usd"], "below_reserve_steps": s["below_reserve_satellite_steps"],
        "brownout_steps": s["brownout_satellite_steps"], "min_soc_pct": s["minimum_soc_pct"],
        "missed_infeasible": causes.get("infeasible_contacts", 0),
        "missed_avoidable": s["jobs_due_missed"] - causes.get("infeasible_contacts", 0),
        "top_avoidable_cause": next((c for c in causes if c != "infeasible_contacts"), None),
        "seconds": round(seconds, 3),
        # share of the provable maximum (LP bound over the base plan, without events)
        "jobs_of_bound": round(s["jobs_completed"] / bound["max_jobs"], 4) if bound and not with_events else None,
        "critical_of_bound": round(s["critical_jobs_completed_on_time"] / bound["max_critical"], 4)
        if bound and bound["max_critical"] and not with_events else None,
        "revenue_of_bound": round(s["revenue_usd"] / bound["max_revenue_usd"], 4)
        if bound and bound["max_revenue_usd"] and not with_events else None,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scenarios", nargs="*", default=sorted(p.stem for p in DATA_DIR.glob("*.json")))
    ap.add_argument("--planners", nargs="*", default=sorted(PLANNERS))
    ap.add_argument("--goals", nargs="*", default=list(GOALS))
    ap.add_argument("--out", type=Path, default=ROOT / "results")
    ap.add_argument("--examples", action="store_true", help="write curated example runs to examples/runs/")
    args = ap.parse_args()

    events = json.loads(EVENTS_FILE.read_text(encoding="utf-8"))["events"]
    if args.examples:
        out = EXAMPLES_DIR / "runs"
        out.mkdir(parents=True, exist_ok=True)
        for scenario_id, goal, with_events in EXAMPLES:
            scenario = load(DATA_DIR / f"{scenario_id}.json")
            session, _ = run_one(scenario, sorted(PLANNERS)[0], goal, events if with_events else [])
            res = session.result()
            res.pop("trace")  # recomputed on replay; keeps the files small
            name = f"{scenario_id}_{goal}{'_events' if with_events else ''}"
            (out / f"{name}.result.json").write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")
            print("wrote", out / f"{name}.result.json")
        return

    args.out.mkdir(parents=True, exist_ok=True)
    rows = []
    for scenario_id in args.scenarios:
        scenario = load(DATA_DIR / f"{scenario_id}.json")
        bound = upper_bounds(scenario)
        variants = [False, True] if scenario["time"]["steps"] >= 288 else [False]
        for planner in args.planners:
            for goal in args.goals:
                for with_events in variants:
                    name = f"{scenario_id}__{planner}__{goal}{'__events' if with_events else ''}"
                    session, sec = run_one(scenario, planner, goal, events if with_events else [])
                    (args.out / f"{name}.result.json").write_text(
                        json.dumps(session.result(), ensure_ascii=False), encoding="utf-8")
                    rows.append(row_for(name, scenario_id, planner, goal, with_events, session, sec, bound))
                    print(f"{name}: done {rows[-1]['jobs_completed']}/{rows[-1]['jobs_total']}, "
                          f"p3 {rows[-1]['critical_on_time']}/{rows[-1]['critical_due']}, "
                          f"${rows[-1]['revenue_usd']:.2f}, {sec:.2f}s")

    with (args.out / "summary.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    cols = ["scenario", "events", "planner", "goal", "jobs_completed", "jobs_total", "critical_on_time",
            "critical_due", "revenue_usd", "jobs_of_bound", "critical_of_bound", "revenue_of_bound",
            "missed_infeasible", "missed_avoidable", "top_avoidable_cause", "below_reserve_steps", "seconds"]
    lines = [f"# Experiments\n\nalgorithm version {ALGORITHM_VERSION}, python {platform.python_version()}, "
             f"events file `{EVENTS_FILE.relative_to(ROOT)}` for 288-step scenarios.\n",
             "| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    lines += ["| " + " | ".join(str(r[c]) for c in cols) + " |" for r in rows]
    (args.out / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("summary:", args.out / "summary.md")


if __name__ == "__main__":
    main()
