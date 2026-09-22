"""Forecasts, comparisons and what-if analyses. None of these change the source run.

Forecasts use only what the run already knows: the known plan and received events.
Future messages are unknown, so a forecast is labelled as such.
"""
from __future__ import annotations

import copy
from collections import defaultdict

from model.operations import Session, digest

from .analysis import miss_causes, trace_index, _by_step
from .planners import make_planner
from .runs import Run, RunStore, check_goal
from .scenarios import apply_overrides

FORECAST_NOTE = "Прогноз по известному плану и полученным сообщениям; будущие сообщения не учитываются."
BRIEF = ("jobs_completed", "jobs_due_missed", "critical_jobs_due", "critical_jobs_completed_on_time",
         "revenue_usd", "blocked_command_count", "below_reserve_satellite_steps",
         "brownout_satellite_steps", "critical_soc_satellite_steps", "minimum_soc_pct",
         "work_steps_in_missed_jobs")


def _run_to_end(session, planner, goal, progress=None, span=(0.0, 1.0), events_by_step=None):
    n = session.env.s["time"]["steps"]
    start = session.env.k
    total = max(1, n - start)
    errors = []
    while session.env.k < n:
        for e in (events_by_step or {}).get(session.env.k, []):
            try:
                session.apply_event(copy.deepcopy(e))
            except ValueError as exc:
                errors.append({"event_id": e.get("id"), "error": str(exc)})
        session.advance(planner.plan(session, goal))
        done = session.env.k - start
        if progress and (done % 16 == 0 or session.env.k == n):
            progress(span[0] + (span[1] - span[0]) * done / total, f"step {session.env.k}/{n}")
    return errors


def _brief(summary: dict) -> dict:
    s = {k: summary[k] for k in BRIEF}
    s["critical_rate"] = (round(summary["critical_jobs_completed_on_time"] / summary["critical_jobs_due"], 6)
                          if summary["critical_jobs_due"] else None)
    soc = summary["terminal_soc_pct"]
    s["mean_terminal_soc_pct"] = round(sum(soc.values()) / len(soc), 6)
    return s


def _delta(a: dict, b: dict) -> dict:
    return {k: (round(b[k] - a[k], 6) if isinstance(a.get(k), (int, float)) and isinstance(b.get(k), (int, float))
                else None) for k in a}


# ---------- forecast and warnings ----------

def _when(step: int, now: int) -> str:
    return "уже на текущем шаге" if step <= now else f"на шаге {step} (через {step - now} шаг.)"


def forecast(run: Run, progress=None) -> dict:
    with run.lock:
        session = run.session.fork()
        planner = copy.deepcopy(run.planner)
        now = run.step
        current = session.summary()
    _run_to_end(session, planner, run.goal, progress)
    projected = session.summary()
    env = session.env
    warnings = []
    first_low = {}
    for r in env.trace:
        if r["step"] < now:
            continue
        sid = r["satellite_id"]
        if r["below_reserve"] and sid not in first_low:
            first_low[sid] = r
    for sid, r in sorted(first_low.items(), key=lambda x: x[1]["step"]):
        cap = env.sats[sid]["capacity_wh"]
        warnings.append({"type": "energy_below_reserve", "satellite_id": sid, "step": r["step"],
                         "in_steps": r["step"] - now,
                         "message": f"{sid}: заряд опустится ниже резерва {_when(r['step'], now)} "
                                    f"({100 * r['energy_after_wh'] / cap:.1f}%)"})
    valid = env.s["model"]["calibration_valid_steps"]
    for sid, st in sorted(run.session.env.state.items()):
        left = valid - st["calibration_age_steps"]
        if left <= 6 and run.session.env.available(sid):
            warnings.append({"type": "calibration_expiring", "satellite_id": sid, "step": now + max(left, 0),
                             "in_steps": max(left, 0),
                             "message": f"{sid}: калибровка истекает {_when(now + max(left, 0), now)}"})
    tindex, by_step = trace_index(env), _by_step(env)
    at_risk = []
    for j in env.jobs.values():
        if j["priority"] == 3 and j["completed_step"] is None and j["deadline_step"] > now:
            c = miss_causes(session, j, tindex, by_step)
            at_risk.append({"id": j["id"], "kind": j["kind"], "deadline_step": j["deadline_step"],
                            "value_usd": j["value_usd"], "cause": c["primary"], "text": c["text"]})
    for j in at_risk:
        warnings.append({"type": "critical_job_at_risk", "job_id": j["id"], "step": j["deadline_step"],
                         "in_steps": j["deadline_step"] - now,
                         "message": f"{j['id']} (приоритет 3) не будет выполнено к шагу {j['deadline_step']}: {j['text']}"})
    return {
        "note": FORECAST_NOTE, "as_of_step": now, "goal": run.goal, "planner": run.planner_name,
        "current": _brief(current), "projected": _brief(projected),
        "delta": _delta(_brief(current), _brief(projected)),
        "warnings": sorted(warnings, key=lambda w: w["step"]),
        "critical_at_risk": at_risk,
    }


# ---------- comparison of two runs ----------

def _events_key(session) -> str:
    return digest(session.events)


def _origin(a: Run, b: Run) -> dict | None:
    for x, y in ((a, b), (b, a)):
        if x.parent and x.parent.get("run_id") == y.id:
            return {"branch": x.id, "from": y.id, "step": x.parent["step"]}
    if a.parent and b.parent and a.parent.get("run_id") == b.parent.get("run_id") and a.parent["step"] == b.parent["step"]:
        return {"from": a.parent["run_id"], "step": a.parent["step"]}
    return None


def verdict(goal: str, a: dict, b: dict, name_a: str = "A", name_b: str = "B") -> dict:
    """Which variant is preferable for the goal, or 'comparable'. a/b are _brief() dicts."""
    check_goal(goal)
    crit = b["critical_jobs_completed_on_time"] - a["critical_jobs_completed_on_time"]
    rev = b["revenue_usd"] - a["revenue_usd"]
    rev_tol = 0.005 * max(abs(a["revenue_usd"]), abs(b["revenue_usd"]), 1.0)
    if goal == "priority":
        if crit:
            best = name_b if crit > 0 else name_a
            reason = f"заданий приоритета 3 в срок: {'+' if crit > 0 else ''}{crit} у {name_b} относительно {name_a}"
        elif abs(rev) > rev_tol:
            best = name_b if rev > 0 else name_a
            reason = f"приоритет 3 одинаков, выручка отличается на {rev:+.2f} $"
        else:
            best, reason = None, "по приоритету 3 и выручке результаты сопоставимы"
    else:
        if abs(rev) > rev_tol:
            best = name_b if rev > 0 else name_a
            reason = f"выручка {name_b} относительно {name_a}: {rev:+.2f} $"
        else:
            best, reason = None, f"разница выручки {rev:+.2f} $ в пределах 0,5% — результаты сопоставимы"
    trade = []
    if goal == "revenue" and crit:
        trade.append(f"приоритет 3 в срок: {crit:+d} у {name_b}")
    if goal == "priority" and crit and abs(rev) > rev_tol:
        trade.append(f"выручка: {rev:+.2f} $ у {name_b}")
    dsoc = b["mean_terminal_soc_pct"] - a["mean_terminal_soc_pct"]
    if abs(dsoc) >= 1:
        trade.append(f"средний конечный заряд: {dsoc:+.1f} п.п. у {name_b}")
    return {"goal": goal, "preferred": best, "comparable": best is None, "reason": reason, "trade_offs": trade}


def compare(a: Run, b: Run, goal: str | None = None, list_limit: int = 30) -> dict:
    goal = goal or a.goal
    sa, sb = a.session, b.session
    notes = []
    same_scenario = a.scenario_hash == b.scenario_hash
    same_events = _events_key(sa) == _events_key(sb)
    if not same_scenario:
        notes.append("Разные исходные сценарии: сравнение отражает разные условия.")
    if not same_events:
        notes.append("Ветви получили разные сообщения.")
    if a.step != b.step:
        notes.append(f"Ветви на разных шагах ({a.step} и {b.step}); показатели относятся к исполненной части.")
    ba, bb = _brief(sa.summary()), _brief(sb.summary())
    ja, jb = sa.env.jobs, sb.env.jobs
    done_a = {j for j, v in ja.items() if v["completed_step"] is not None}
    done_b = {j for j, v in jb.items() if v["completed_step"] is not None}

    def listing(ids, jobs):
        items = sorted((jobs[i] for i in ids), key=lambda j: (-j["priority"], -j["value_usd"]))
        return {"count": len(items), "value_usd": round(sum(j["value_usd"] for j in items), 6),
                "critical": sum(j["priority"] == 3 for j in items),
                "items": [{"id": j["id"], "kind": j["kind"], "priority": j["priority"], "value_usd": j["value_usd"]}
                          for j in items[:list_limit]]}

    return {
        "a": {"run_id": a.id, "label": a.label, "goal": a.goal, "planner": a.planner_name, "step": a.step},
        "b": {"run_id": b.id, "label": b.label, "goal": b.goal, "planner": b.planner_name, "step": b.step},
        "same_conditions": same_scenario and same_events,
        "origin": _origin(a, b),
        "notes": notes,
        "metrics": {"a": ba, "b": bb, "delta": _delta(ba, bb)},
        "only_a_completed": listing(done_a - done_b, ja),
        "only_b_completed": listing(done_b - done_a, jb),
        "verdict": verdict(goal, ba, bb, "A", "B"),
    }


# ---------- strategy sweep from the current state ----------

def strategies(store: RunStore, run: Run, candidates: list[dict], rank_goal: str | None,
               progress=None) -> dict:
    rank_goal = check_goal(rank_goal or run.goal)
    if not candidates:
        raise ValueError("candidates must be a nonempty list")
    rows = []
    for i, c in enumerate(candidates):
        goal = c.get("goal") or run.goal
        with run.lock:
            branch = store.fork(run, goal=goal, planner=c.get("planner"), params=c.get("params"),
                                label=c.get("label") or f"{c.get('planner') or run.planner_name}/{goal}",
                                persist=False)
        span = (i / len(candidates), (i + 1) / len(candidates))
        _run_to_end(branch.session, branch.planner, branch.goal, progress, span)
        store.save(branch)
        store._remember(branch)
        rows.append({"run_id": branch.id, "label": branch.label, "planner": branch.planner_name,
                     "goal": branch.goal, "params": branch.planner_params,
                     "metrics": _brief(branch.session.summary())})
    key = ((lambda r: (r["metrics"]["critical_jobs_completed_on_time"], r["metrics"]["revenue_usd"]))
           if rank_goal == "priority" else
           (lambda r: (r["metrics"]["revenue_usd"], r["metrics"]["critical_jobs_completed_on_time"])))
    ranked = sorted(rows, key=key, reverse=True)
    best, second = ranked[0], ranked[1] if len(ranked) > 1 else None
    v = verdict(rank_goal, second["metrics"], best["metrics"], second["label"], best["label"]) if second else None
    return {"note": FORECAST_NOTE, "from_step": run.step, "rank_goal": rank_goal,
            "ranking": ranked, "best": best["run_id"], "verdict_vs_runner_up": v}


# ---------- what-if: accept an urgent request? ----------

def whatif_event(run: Run, event: dict, progress=None) -> dict:
    with run.lock:
        with_s, without_s = run.session.fork(), run.session.fork()
        p_with, p_without = copy.deepcopy(run.planner), copy.deepcopy(run.planner)
    ev = copy.deepcopy(event)
    ev.setdefault("at_step", run.step)
    ev.setdefault("id", f"WHATIF-{run.step}")
    with_s.apply_event(ev)  # raises ValueError with a readable reason
    _run_to_end(without_s, p_without, run.goal, progress, (0.0, 0.5))
    _run_to_end(with_s, p_with, run.goal, progress, (0.5, 1.0))
    a, b = _brief(without_s.summary()), _brief(with_s.summary())
    old = without_s.env.jobs
    lost = [j for j in old if old[j]["completed_step"] is not None and with_s.env.jobs[j]["completed_step"] is None]
    gained_old = [j for j in old if old[j]["completed_step"] is None and with_s.env.jobs[j]["completed_step"] is not None]
    new_ids = [j for j in with_s.env.jobs if j not in old]
    new_done = [j for j in new_ids if with_s.env.jobs[j]["completed_step"] is not None]

    def items(ids, jobs):
        return [{"id": i, "priority": jobs[i]["priority"], "value_usd": jobs[i]["value_usd"], "kind": jobs[i]["kind"]}
                for i in sorted(ids, key=lambda i: (-jobs[i]["priority"], -jobs[i]["value_usd"]))]

    d = _delta(a, b)
    return {
        "note": FORECAST_NOTE, "as_of_step": run.step, "event": ev,
        "without": a, "with": b, "delta": d,
        "new_jobs": {"total": len(new_ids), "completed": items(new_done, with_s.env.jobs),
                     "not_completed": items(set(new_ids) - set(new_done), with_s.env.jobs)},
        "displaced_jobs": items(lost, old),
        "newly_completed_existing_jobs": items(gained_old, with_s.env.jobs),
        "verdict": verdict(run.goal, a, b, "без сообщения", "с сообщением"),
        "summary": (f"С сообщением: выручка {d['revenue_usd']:+.2f} $, приоритет 3 в срок "
                    f"{d['critical_jobs_completed_on_time']:+d}; вытеснено заданий: {len(lost)}, "
                    f"новых выполнено: {len(new_done)} из {len(new_ids)}."),
    }


# ---------- what-if: more resources (new experiment from step 0) ----------

def whatif_resources(run: Run, variations: list[dict], progress=None) -> dict:
    if not variations:
        raise ValueError("variations must be a nonempty list")
    base = run.session.initial_scenario
    events_by_step = defaultdict(list)
    for e in run.session.events:
        events_by_step[e["at_step"]].append(e)
    all_vars = [{"label": "как есть", "overrides": {}}] + list(variations)
    results = []
    for i, v in enumerate(all_vars):
        scenario = apply_overrides(base, v.get("overrides") or {})
        session = Session(scenario)
        planner = make_planner(run.planner_name, run.planner_params)
        errors = _run_to_end(session, planner, run.goal, progress,
                             (i / len(all_vars), (i + 1) / len(all_vars)), events_by_step)
        results.append({"label": v.get("label") or f"вариант {i}", "overrides": v.get("overrides") or {},
                        "metrics": _brief(session.summary()), "event_errors": errors})
    b0 = results[0]["metrics"]
    for r in results[1:]:
        r["delta"] = _delta(b0, r["metrics"])
        r["verdict"] = verdict(run.goal, b0, r["metrics"], "как есть", r["label"])
    return {"note": "Каждый вариант — новый эксперимент с шага 0 с теми же сообщениями, что получил запуск.",
            "goal": run.goal, "planner": run.planner_name, "results": results}
