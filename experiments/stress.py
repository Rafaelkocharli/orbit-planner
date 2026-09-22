"""Stress check: conditions the planners were not tuned on.

The jury may load another scenario of the same format and send events one by one, so the
planners are checked on variants of the published data and on random event streams:

- S1_energy_overload  P04 queue (8120 jobs) with the P03 energy deficit;
- S2_single_channel   P02 with one ground channel instead of two;
- S3_half_fleet       P02 cut to S01-S24 and the first 144 steps (another problem size);
- S4_known_failures   P02 with outages of 8 satellites known from the start;
- event streams       6 random events (outages, closed contacts, urgent jobs) for P02 and
                      P03, three seeds each; events are generated valid for the scenario.

Everything is deterministic (fixed seeds). Results go to results/stress/: result files,
summary.csv, summary.md and generated inputs (scenarios/, events/).

    python -m experiments.stress
    python -m experiments.stress --planners reserve greedy-edf --skip-bounds
"""
from __future__ import annotations

import argparse
import copy
import csv
import json
import random

from model.resource_env import load, validate

from backend.app.config import DATA_DIR, ROOT
from backend.app.planners import GOALS, PLANNERS
from experiments.run_experiments import row_for, run_one
from experiments.upper_bound import bounds as upper_bounds

OUT = ROOT / "results" / "stress"
EVENT_SEEDS = (11, 22, 33)


def base(name: str) -> dict:
    return load(DATA_DIR / f"{name}.json")


def s1_energy_overload() -> dict:
    s, energy = base("P04_demand"), base("P03_energy")
    s["satellites"] = copy.deepcopy(energy["satellites"])
    for sid, env in s["environment"].items():
        env["solar_w"] = list(energy["environment"][sid]["solar_w"])
    return s


def s2_single_channel() -> dict:
    s = base("P02_shift")
    s["model"]["downlink_parallel_limit"] = 1
    return s


def s3_half_fleet() -> dict:
    s = base("P02_shift")
    keep = {f"S{i:02d}" for i in range(1, 25)}
    n = 144
    s["time"]["steps"] = n
    s["satellites"] = [v for v in s["satellites"] if v["id"] in keep]
    s["environment"] = {sid: {k: arr[:n] for k, arr in env.items()}
                        for sid, env in s["environment"].items() if sid in keep}
    jobs = []
    for j in s["jobs"]:
        eligible = [x for x in j["eligible_satellites"] if x in keep]
        if eligible and j["deadline_step"] <= n:
            jobs.append(dict(j, eligible_satellites=eligible))
    s["jobs"] = jobs
    return s


def s4_known_failures() -> dict:
    s = base("P02_shift")
    rng = random.Random(4)
    for sid in rng.sample([v["id"] for v in s["satellites"]], 8):
        start = rng.randrange(0, 240)
        s["failures"].append({"satellite_id": sid, "start_step": start,
                              "end_step": min(288, start + rng.randrange(24, 60))})
    return s


VARIANTS = {
    "S1_energy_overload": ("Перегрузка при дефиците энергии", s1_energy_overload),
    "S2_single_channel": ("Один канал связи с Землёй", s2_single_channel),
    "S3_half_fleet": ("24 аппарата, 12 часов", s3_half_fleet),
    "S4_known_failures": ("Известные отказы 8 аппаратов", s4_known_failures),
}


def event_stream(s: dict, seed: int, count: int = 6) -> list[dict]:
    """Valid random events in increasing order: outages, closed contacts, urgent jobs."""
    rng = random.Random(seed)
    n = s["time"]["steps"]
    sats = [v["id"] for v in s["satellites"]]
    steps = sorted(rng.sample(range(24, n - 24), count))
    kinds = ["satellite_outage", "close_downlink", "add_jobs"] * (count // 3 + 1)
    rng.shuffle(kinds)
    events = []
    for i, (at, kind) in enumerate(zip(steps, kinds)):
        e = {"id": f"R{seed}-{i + 1:02d}", "at_step": at, "type": kind}
        if kind in ("satellite_outage", "close_downlink"):
            e["satellite_ids"] = sorted(rng.sample(sats, rng.randint(2, 6)))
            e["end_step"] = min(n, at + rng.randint(6, 36))
        else:
            jobs = []
            for q in range(rng.randint(2, 5)):
                kind_j = rng.choice(["relay", "relay", "downlink"])
                window = rng.randint(6, 16)
                work = rng.randint(1, 3 if kind_j == "relay" else 2)
                eligible = sorted(rng.sample(sats, 3)) if kind_j == "relay" else [rng.choice(sats)]
                jobs.append({"id": f"R{seed}-{i + 1:02d}-J{q + 1}", "kind": kind_j,
                             "release_step": at, "deadline_step": min(n, at + window),
                             "work_steps": min(work, window), "eligible_satellites": eligible,
                             "priority": rng.choice([3, 3, 2]),
                             "value_usd": round(rng.uniform(15, 60), 2)})
            e["jobs"] = jobs
        events.append(e)
    return events


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--planners", nargs="*", default=list(PLANNERS))
    ap.add_argument("--goals", nargs="*", default=list(GOALS))
    ap.add_argument("--skip-bounds", action="store_true")
    args = ap.parse_args()
    (OUT / "scenarios").mkdir(parents=True, exist_ok=True)
    (OUT / "events").mkdir(parents=True, exist_ok=True)

    cases = []  # (case name, scenario id, scenario, events, bound)
    for sid, (title, make) in VARIANTS.items():
        s = make()
        s["meta"] = {"id": sid, "title": title}
        validate(s)
        (OUT / "scenarios" / f"{sid}.json").write_text(json.dumps(s, ensure_ascii=False), encoding="utf-8")
        bound = None if args.skip_bounds else upper_bounds(s)
        if bound:
            print(sid, "bound", json.dumps(bound, ensure_ascii=False), flush=True)
        cases.append((sid, sid, s, [], bound))
    for name in ("P02_shift", "P03_energy"):
        s = base(name)
        for seed in EVENT_SEEDS:
            ev = event_stream(s, seed)
            (OUT / "events" / f"{name}_seed{seed}.json").write_text(json.dumps(
                {"schema_version": "cosmo-B-events-1.0", "base_scenario": name, "events": ev},
                ensure_ascii=False, indent=1), encoding="utf-8")
            cases.append((f"{name}+events{seed}", name, s, ev, None))

    rows = []
    for case, sid, s, ev, bound in cases:
        for planner in args.planners:
            for goal in args.goals:
                session, sec = run_one(s, planner, goal, ev)
                name = f"{case}__{planner}__{goal}"
                (OUT / f"{name}.result.json").write_text(json.dumps(session.result(), ensure_ascii=False),
                                                         encoding="utf-8")
                row = row_for(name, case, planner, goal, bool(ev), session, sec, bound)
                row["events_rejected"] = len(ev) - len(session.events)
                rows.append(row)
                print(f"{name}: done {row['jobs_completed']}/{row['jobs_total']}, p3 {row['critical_on_time']}/"
                      f"{row['critical_due']}, ${row['revenue_usd']:.2f}, blocked "
                      f"{session.summary()['blocked_command_count']}, {sec:.1f}s", flush=True)

    with (OUT / "summary.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    cols = ["scenario", "planner", "goal", "jobs_completed", "jobs_total", "critical_on_time", "critical_due",
            "revenue_usd", "jobs_of_bound", "critical_of_bound", "revenue_of_bound", "missed_infeasible",
            "missed_avoidable", "top_avoidable_cause", "below_reserve_steps", "brownout_steps", "seconds"]
    lines = ["# Stress check", "", "| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    lines += ["| " + " | ".join(str(r[c]) for c in cols) + " |" for r in rows]
    (OUT / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("summary:", OUT / "summary.md")


if __name__ == "__main__":
    main()
