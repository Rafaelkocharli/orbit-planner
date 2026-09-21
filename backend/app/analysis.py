"""Explanations for the operator (O3). Keep causes of blocked actions and missed jobs separate."""
from __future__ import annotations


def job_outcome(env, job: dict) -> dict:
    """Classify a job at the current step. 'infeasible_contacts' is a provable bound:
    the window holds fewer contact steps for any eligible satellite than the remaining work."""
    k = env.k
    if job["completed_step"] is not None:
        return {"status": "completed", "completed_step": job["completed_step"]}
    key = job["kind"] + "_available"
    start = max(k, job["release_step"])
    contacts = sum(
        any(env.s["environment"][sid][key][t] for sid in job["eligible_satellites"])
        for t in range(start, job["deadline_step"])
    )
    done = job["work_steps"] - job["remaining_steps"]
    if job["deadline_step"] <= k:
        status = "missed"
    elif contacts < job["remaining_steps"]:
        status = "infeasible_contacts"
    else:
        status = "open"
    return {"status": status, "contact_steps_left": contacts, "work_done": done}


def jobs_report(env) -> list[dict]:
    return [
        {**{f: j[f] for f in ("id", "kind", "priority", "value_usd", "release_step",
                              "deadline_step", "work_steps", "remaining_steps",
                              "eligible_satellites")},
         **job_outcome(env, j)}
        for j in env.jobs.values()
    ]
