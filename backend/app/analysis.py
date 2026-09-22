"""Explanations for the operator (O3, T1).

Causes of a blocked action and causes of a missed job are kept separate:
- a blocked command is a fact of one step (reason from the model);
- a missed job gets a cause derived from every opportunity it had in its window.
"infeasible_contacts" is a provable bound, everything else is an attribution.
"""
from __future__ import annotations

import math
from collections import Counter, defaultdict

from model.operations import replay_episode

NOT_BLOCKED = ("accepted", "idle")

# Order used to break ties when choosing the primary cause of a miss.
CAUSE_ORDER = ("infeasible_contacts", "busy_other_job", "ground_capacity", "calibration_expired",
               "energy_reserve", "thermal_limit", "calibrating", "satellite_unavailable",
               "blocked", "not_scheduled", "received_too_late")

CAUSE_TEXT = {
    "infeasible_contacts": "в окне задания меньше доступных шагов связи, чем нужно работы — выполнить нельзя ни при каких действиях",
    "busy_other_job": "допустимые аппараты в шаги связи выполняли другие задания",
    "ground_capacity": "оба канала связи с Землёй были заняты другими аппаратами",
    "calibration_expired": "калибровка аппарата истекла к моменту связи",
    "energy_reserve": "операция опустила бы заряд ниже резерва",
    "thermal_limit": "температура вне рабочего диапазона",
    "calibrating": "аппарат калибровался в шаг связи",
    "satellite_unavailable": "аппарат был недоступен",
    "blocked": "команда была отклонена моделью",
    "not_scheduled": "условия позволяли, но планировщик выбрал другое действие (недостаток алгоритма)",
    "received_too_late": "задание поступило, когда времени уже не хватало",
}


# ---------- helpers over the model ----------

def unavailable(env, sid: str, t: int) -> bool:
    return any(f["satellite_id"] == sid and f["start_step"] <= t < f["end_step"]
               for f in env.s["failures"])


def reserve_wh(env, sid: str) -> float:
    return env.sats[sid]["capacity_wh"] * env.s["model"]["reserve_soc_pct"] / 100


def simulate(env, sid: str, t: int, energy: float, temp: float, payload_w: float):
    """Same transition as resource_env.Environment.transition, for any step and state."""
    v, m, e = env.sats[sid], env.s["model"], env.s["environment"][sid]
    dt = env.s["time"]["step_s"]
    heater = v["heater_w"] if temp < m["heater_below_c"] else 0.0
    load = v["base_w"] + heater + payload_w
    delta = (e["solar_w"][t] - load) * dt / 3600
    if delta >= 0:
        delta = delta * m["charge_efficiency"] if m["charge_min_c"] <= temp <= m["charge_max_c"] else 0.0
    else:
        delta /= m["discharge_efficiency"]
    eq = e["thermal_target_c"][t] + m["thermal_gain_c_per_w"] * load
    return energy + delta, eq + (temp - eq) * math.exp(-dt / m["thermal_tau_s"])


def resource_check(env, sid: str, t: int, energy: float, temp: float, payload_w: float) -> str | None:
    """Energy and thermal admission exactly as in can_execute; None if admissible."""
    m = env.s["model"]
    end_e, end_t = simulate(env, sid, t, energy, temp, payload_w)
    r = reserve_wh(env, sid) - 1e-9
    if energy < r or end_e < r:
        return "energy_reserve"
    if not (m["payload_min_c"] <= temp <= m["payload_max_c"] and m["payload_min_c"] <= end_t <= m["payload_max_c"]):
        return "thermal_limit"
    return None


def trace_index(env) -> dict:
    return {(r["step"], r["satellite_id"]): r for r in env.trace}


def downlinks_at(env, idx_by_step: dict, t: int) -> list[str]:
    return [r["requested"].get("job_id") for r in idx_by_step.get(t, [])
            if r["executed"] == "job" and env.jobs.get(r["requested"].get("job_id"), {}).get("kind") == "downlink"]


def _received_step(session, job_id: str) -> int:
    for e in session.events:
        if e["type"] == "add_jobs" and any(j["id"] == job_id for j in e["jobs"]):
            return e["at_step"]
    return 0


# ---------- jobs ----------

def usable_steps(env, job: dict, start: int | None = None) -> list[int]:
    """Steps in the window where at least one eligible satellite has contact and is available,
    according to everything received so far (closed contacts and outages included)."""
    key = job["kind"] + "_available"
    lo = max(job["release_step"], start if start is not None else 0)
    return [t for t in range(lo, job["deadline_step"])
            if any(env.s["environment"][sid][key][t] and not unavailable(env, sid, t)
                   for sid in job["eligible_satellites"])]


def job_outcome(env, job: dict) -> dict:
    k = env.k
    done = job["work_steps"] - job["remaining_steps"]
    if job["completed_step"] is not None:
        return {"status": "completed", "completed_step": job["completed_step"], "work_done": done}
    usable_left = usable_steps(env, job, start=k)
    if job["deadline_step"] <= k:
        status = "missed"
    elif job["release_step"] > k:
        status = "pending" if len(usable_left) >= job["remaining_steps"] else "infeasible"
    else:
        status = "open" if len(usable_left) >= job["remaining_steps"] else "infeasible"
    return {"status": status, "work_done": done, "usable_steps_left": len(usable_left)}


def jobs_report(env) -> list[dict]:
    fields = ("id", "kind", "priority", "value_usd", "release_step", "deadline_step",
              "work_steps", "remaining_steps", "eligible_satellites")
    executors = defaultdict(set)
    for r in env.trace:
        if r["executed"] == "job":
            executors[r["requested"]["job_id"]].add(r["satellite_id"])
    return [{**{f: j[f] for f in fields}, **job_outcome(env, j),
             "executors": sorted(executors.get(j["id"], ()))}
            for j in env.jobs.values()]


def infeasibility_proof(env, job: dict) -> dict | None:
    """Verifiable proof that a job could not be finished whatever the actions were."""
    usable = usable_steps(env, job)
    if len(usable) >= job["work_steps"]:
        return None
    return {
        "window": [job["release_step"], job["deadline_step"]],
        "work_steps": job["work_steps"],
        "usable_steps": usable,
        "statement": (f"В окне [{job['release_step']}; {job['deadline_step']}) у допустимых аппаратов "
                      f"{', '.join(job['eligible_satellites'])} есть {len(usable)} шаг(ов) со связью "
                      f"({job['kind']}_available и аппарат доступен), а нужно {job['work_steps']}."),
    }


def miss_causes(session, job: dict, tindex: dict | None = None, by_step: dict | None = None) -> dict:
    env = session.env
    proof = infeasibility_proof(env, job)
    if proof:
        return {"primary": "infeasible_contacts", "text": CAUSE_TEXT["infeasible_contacts"],
                "proof": proof, "breakdown": {}}
    tindex = tindex if tindex is not None else trace_index(env)
    by_step = by_step if by_step is not None else _by_step(env)
    kind, key = job["kind"], job["kind"] + "_available"
    received = _received_step(session, job["id"])
    counts: Counter = Counter()
    competitors: Counter = Counter()
    limit = env.s["model"]["downlink_parallel_limit"]
    for t in range(job["release_step"], min(job["deadline_step"], env.k)):
        for sid in job["eligible_satellites"]:
            if not env.s["environment"][sid][key][t]:
                continue
            if unavailable(env, sid, t):
                counts["satellite_unavailable"] += 1
                continue
            row = tindex.get((t, sid))
            if row is None:
                continue
            req = row["requested"]
            if row["executed"] == "job":
                if req.get("job_id") == job["id"]:
                    counts["worked"] += 1
                else:
                    counts["busy_other_job"] += 1
                    competitors[req["job_id"]] += 1
                continue
            if row["executed"] == "calibrate":
                counts["calibrating"] += 1
                continue
            if req.get("action", "idle") != "idle" and row["reason"] not in NOT_BLOCKED:
                counts[row["reason"] if row["reason"] in CAUSE_TEXT else "blocked"] += 1
                continue
            # the satellite idled: would this job have been admissible?
            if any(r["executed"] == "job" and r["requested"].get("job_id") == job["id"]
                   for r in by_step.get(t, [])):
                counts["worked"] += 1  # relay taken by another eligible satellite
                continue
            age_before = row["calibration_age_steps"] - 1
            if age_before >= env.s["model"]["calibration_valid_steps"]:
                counts["calibration_expired"] += 1
                continue
            res = resource_check(env, sid, t, row["energy_before_wh"], row["temp_before_c"],
                                 env.sats[sid][kind + "_w"])
            if res:
                counts[res] += 1
            elif kind == "downlink" and len(downlinks_at(env, by_step, t)) >= limit:
                counts["ground_capacity"] += 1
            else:
                counts["not_scheduled"] += 1
    lost = {c: n for c, n in counts.items() if c != "worked"}
    if not lost:
        primary = "received_too_late" if received > job["release_step"] else "blocked"
    else:
        primary = max(lost, key=lambda c: (lost[c], -CAUSE_ORDER.index(c)))
    out = {"primary": primary, "text": CAUSE_TEXT[primary], "breakdown": dict(counts)}
    if competitors:
        out["competing_jobs"] = [
            {"id": jid, "priority": env.jobs[jid]["priority"], "value_usd": env.jobs[jid]["value_usd"], "steps": n}
            for jid, n in competitors.most_common(5)]
    return out


def _by_step(env) -> dict:
    by_step = defaultdict(list)
    for r in env.trace:
        by_step[r["step"]].append(r)
    return by_step


def missed_jobs(session, limit: int | None = None) -> dict:
    env = session.env
    tindex, by_step = trace_index(env), _by_step(env)
    missed = [j for j in env.jobs.values() if j["completed_step"] is None and j["deadline_step"] <= env.k]
    missed.sort(key=lambda j: (-j["priority"], -j["value_usd"]))
    items, totals, value_by = [], Counter(), Counter()
    for j in missed:
        c = miss_causes(session, j, tindex, by_step)
        totals[c["primary"]] += 1
        value_by[c["primary"]] += j["value_usd"]
        items.append({"id": j["id"], "kind": j["kind"], "priority": j["priority"],
                      "value_usd": j["value_usd"], "window": [j["release_step"], j["deadline_step"]],
                      "work_done": j["work_steps"] - j["remaining_steps"], "work_steps": j["work_steps"],
                      **c})
    return {
        "count": len(missed),
        "by_cause": [{"cause": c, "text": CAUSE_TEXT[c], "jobs": n, "value_usd": round(value_by[c], 6),
                      "avoidable": c != "infeasible_contacts"}
                     for c, n in totals.most_common()],
        "jobs": items if limit is None else items[:limit],
    }


# ---------- fleet ----------

def by_priority(env) -> list[dict]:
    out = []
    for p in (3, 2, 1):
        js = [j for j in env.jobs.values() if j["priority"] == p]
        due = [j for j in js if j["deadline_step"] <= env.k]
        done = [j for j in js if j["completed_step"] is not None]
        on_time_due = sum(j["completed_step"] is not None for j in due)
        out.append({
            "priority": p, "total": len(js), "due": len(due), "completed": len(done),
            "completed_on_time_of_due": on_time_due,
            "rate": round(on_time_due / len(due), 6) if due else None,
            "revenue_usd": round(sum(j["value_usd"] for j in done), 6),
            "value_lost_usd": round(sum(j["value_usd"] for j in due if j["completed_step"] is None), 6),
        })
    return out


def utilization(env) -> dict:
    per = {sid: Counter() for sid in env.sats}
    for r in env.trace:
        c = per[r["satellite_id"]]
        c[r["executed"]] += 1
        if r["reason"] not in NOT_BLOCKED:
            c["blocked"] += 1
    n = env.k
    sats = {}
    for sid, c in per.items():
        unavail = sum(unavailable(env, sid, t) for t in range(n))
        sats[sid] = {
            "job_steps": c["job"], "calibrate_steps": c["calibrate"], "idle_steps": c["idle"],
            "blocked_commands": c["blocked"], "unavailable_steps": unavail,
            "utilization": round(c["job"] / n, 6) if n else None,
        }
    job_total = sum(s["job_steps"] for s in sats.values())
    return {"steps_executed": n,
            "fleet_utilization": round(job_total / (n * len(sats)), 6) if n else None,
            "satellites": sats}


def deficit_periods(env) -> list[dict]:
    """Contiguous intervals where the end-of-step charge was below the reserve."""
    out = []
    rows = defaultdict(list)
    for r in env.trace:
        rows[r["satellite_id"]].append(r)
    crit = env.s["model"]["critical_soc_pct"]
    for sid, rs in rows.items():
        cap = env.sats[sid]["capacity_wh"]
        cur = None
        for r in rs:
            soc = 100 * r["energy_after_wh"] / cap
            if r["below_reserve"]:
                if cur is None:
                    cur = {"satellite_id": sid, "start_step": r["step"], "min_soc_pct": soc,
                           "critical": False, "brownout": False}
                cur["end_step"] = r["step"] + 1
                cur["min_soc_pct"] = round(min(cur["min_soc_pct"], soc), 6)
                cur["critical"] |= soc < crit
                cur["brownout"] |= r["brownout"]
            elif cur:
                out.append(cur)
                cur = None
        if cur:
            out.append(cur)
    return sorted(out, key=lambda d: (d["start_step"], d["satellite_id"]))


def blocked_commands(env, limit: int = 500) -> dict:
    rows = [r for r in env.trace if r["reason"] not in NOT_BLOCKED]
    return {
        "count": len(rows),
        "by_reason": dict(Counter(r["reason"] for r in rows)),
        "items": [{"step": r["step"], "satellite_id": r["satellite_id"], "requested": r["requested"],
                   "reason": r["reason"]} for r in rows[:limit]],
    }


def analytics(session) -> dict:
    env = session.env
    s = session.summary()
    due = s["critical_jobs_due"]
    return {
        "step": env.k,
        "summary": s,
        "critical_rate": round(s["critical_jobs_completed_on_time"] / due, 6) if due else None,
        "by_priority": by_priority(env),
        "utilization": utilization(env),
        "deficit_periods": deficit_periods(env),
        "blocked_commands": blocked_commands(env, limit=100),
        "missed": missed_jobs(session, limit=200),
    }


# ---------- one decision ----------

def explain_decision(session, step: int, sid: str, max_options: int = 25) -> dict:
    """What was known at `step`, what the satellite could do, what it did and why."""
    env = session.env
    if not 0 <= step < env.k:
        raise ValueError(f"Step {step} has not been executed yet (executed: {env.k})")
    if sid not in env.sats:
        raise ValueError(f"Unknown satellite {sid}")
    past = replay_episode(session.initial_scenario, [e for e in session.events if e["at_step"] <= step],
                          [c for c in session.commands if c["step"] < step], until_step=step)
    penv = past.env
    row = next(r for r in env.trace if r["step"] == step and r["satellite_id"] == sid)
    st = penv.state[sid]
    same_step = [r for r in env.trace if r["step"] == step]
    taken = {r["requested"]["job_id"]: r["satellite_id"] for r in same_step
             if r["executed"] == "job" and r["satellite_id"] != sid}
    dl_used = [r["satellite_id"] for r in same_step if r["executed"] == "job" and r["satellite_id"] != sid
               and penv.jobs[r["requested"]["job_id"]]["kind"] == "downlink"]
    options = [{"action": "calibrate", **_verdict(penv.can_execute(sid, {"action": "calibrate"}))}]
    open_jobs = [j for j in penv.jobs.values() if sid in j["eligible_satellites"]
                 and j["completed_step"] is None and j["release_step"] <= step < j["deadline_step"]]
    open_jobs.sort(key=lambda j: (-j["priority"], j["deadline_step"]))
    for j in open_jobs[:max_options]:
        ok, reason, _ = penv.can_execute(sid, {"action": "job", "job_id": j["id"]})
        if ok and j["id"] in taken:
            ok, reason = False, f"taken_by_{taken[j['id']]}"
        if ok and j["kind"] == "downlink" and len(dl_used) >= penv.s["model"]["downlink_parallel_limit"]:
            ok, reason = False, "ground_capacity"
        options.append({"action": "job", "job_id": j["id"], "kind": j["kind"], "priority": j["priority"],
                        "value_usd": j["value_usd"], "deadline_step": j["deadline_step"],
                        "remaining_steps": j["remaining_steps"], "admissible": ok, "reason": reason})
    return {
        "step": step, "satellite_id": sid,
        "known": {
            "events_received": [{"id": e["id"], "type": e["type"], "at_step": e["at_step"]} for e in past.events],
            "jobs_known": len(penv.jobs),
            "open_jobs_for_satellite": len(open_jobs),
        },
        "state_before": {
            "energy_wh": round(st["energy_wh"], 6),
            "soc_pct": round(100 * st["energy_wh"] / penv.sats[sid]["capacity_wh"], 6),
            "temp_c": round(st["temp_c"], 6),
            "calibration_age_steps": st["calibration_age_steps"],
            "calibration_valid_steps": penv.s["model"]["calibration_valid_steps"],
            "available": penv.available(sid),
            "downlink_contact": penv.s["environment"][sid]["downlink_available"][step],
            "relay_contact": penv.s["environment"][sid]["relay_available"][step],
            "solar_w": penv.s["environment"][sid]["solar_w"][step],
            "reserve_wh": reserve_wh(penv, sid),
            "downlinks_used_by_others": dl_used,
        },
        "decision": {"requested": row["requested"], "executed": row["executed"], "reason": row["reason"],
                     "completed_job": row["completed_job"]},
        "consequences": {"energy_after_wh": row["energy_after_wh"], "temp_after_c": row["temp_after_c"],
                         "calibration_age_after": row["calibration_age_steps"],
                         "below_reserve": row["below_reserve"], "brownout": row["brownout"]},
        "options": options,
    }


def _verdict(res) -> dict:
    ok, reason, _ = res
    return {"admissible": ok, "reason": reason}
