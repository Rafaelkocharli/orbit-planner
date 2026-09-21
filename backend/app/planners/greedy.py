"""Baseline rule for comparison (T4): earliest deadline first with goal-dependent ordering.

- calibrate when calibration expires next step;
- skip jobs that can no longer finish before the deadline;
- respect the downlink parallel limit and one executor per job ourselves.
"""
from __future__ import annotations


class GreedyPlanner:
    name = "greedy-edf"
    version = "0.1.0"

    def __init__(self, calibrate_margin: int = 1):
        self.calibrate_margin = calibrate_margin

    def params(self) -> dict:
        return {"calibrate_margin": self.calibrate_margin}

    def _key(self, goal: str):
        if goal == "priority":
            return lambda j: (-j["priority"], j["deadline_step"], -j["value_usd"])
        return lambda j: (j["deadline_step"], -j["value_usd"] / j["remaining_steps"])

    def plan(self, session, goal: str) -> dict[str, dict]:
        env = session.env
        k, m = env.k, env.s["model"]
        actions: dict[str, dict] = {}
        downlinks = 0

        for sid in sorted(env.sats):
            age = env.state[sid]["calibration_age_steps"]
            if age >= m["calibration_valid_steps"] - self.calibrate_margin:
                if env.can_execute(sid, {"action": "calibrate"})[0]:
                    actions[sid] = {"action": "calibrate"}

        open_jobs = [
            j for j in env.jobs.values()
            if j["completed_step"] is None
            and j["release_step"] <= k < j["deadline_step"]
            and j["remaining_steps"] <= j["deadline_step"] - k
        ]
        open_jobs.sort(key=self._key(goal))

        for j in open_jobs:
            if j["kind"] == "downlink" and downlinks >= m["downlink_parallel_limit"]:
                continue
            for sid in sorted(j["eligible_satellites"]):
                if sid in actions:
                    continue
                action = {"action": "job", "job_id": j["id"]}
                if env.can_execute(sid, action)[0]:
                    actions[sid] = action
                    downlinks += j["kind"] == "downlink"
                    break
        return actions
