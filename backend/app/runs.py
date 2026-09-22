"""Runs: each run owns an independent Session, planner instance and history.

- Runs belong to a client id; other clients cannot see them.
- fork() deep-copies session and planner, so branches never share state.
- Every mutation is persisted (scenario by hash + events + commands); a run evicted
  from memory is restored by replaying its commands.
"""
from __future__ import annotations

import copy
import threading
import time
import uuid
from collections import OrderedDict
from typing import Callable

from model.operations import RESULT_SCHEMA, Session, digest, replay_episode

from .config import ALGORITHM_VERSION, MAX_RUNS_IN_MEMORY
from .planners import DEFAULT_PLANNER, GOALS, PLANNERS, make_planner, restore
from .storage import Storage


def check_goal(goal: str) -> str:
    if goal not in GOALS:
        raise ValueError(f"goal must be one of {GOALS}")
    return goal


class Run:
    def __init__(self, *, id: str, owner: str, session: Session, planner: str, params: dict,
                 goal: str, scenario_hash: str, label: str = "", goal_history=None,
                 parent: dict | None = None, created_at: float | None = None,
                 planner_obj=None):
        self.id, self.owner, self.session = id, owner, session
        self.planner_name, self.planner_params = planner, copy.deepcopy(params or {})
        self.goal = check_goal(goal)
        self.scenario_hash = scenario_hash
        self.label = label
        self.goal_history = goal_history or []
        self.parent = parent
        self.created_at = created_at or time.time()
        # Plain Lock: a background task may release it from a worker thread.
        self.lock = threading.Lock()
        self.planner = planner_obj or make_planner(planner, self.planner_params)
        self.sync_metadata()

    # ---- metadata required in the export ----
    def sync_metadata(self) -> None:
        md = {
            "goal": self.goal,
            "algorithm": self.planner.name,
            "version": f"{ALGORITHM_VERSION}/{self.planner.version}",
            "parameters": self.planner.params(),
            "goal_switches": copy.deepcopy(self.goal_history),
            "run_id": self.id,
        }
        if self.parent:
            md["forked_from"] = copy.deepcopy(self.parent)
        self.session.run_metadata = md

    @property
    def step(self) -> int:
        return self.session.env.k

    @property
    def steps(self) -> int:
        return self.session.env.s["time"]["steps"]

    @property
    def finished(self) -> bool:
        return self.step >= self.steps

    def set_goal(self, goal: str) -> None:
        check_goal(goal)
        if goal != self.goal:
            self.goal_history.append({"step": self.step, "goal": goal})
            self.goal = goal
            self.sync_metadata()

    def advance(self, n: int, progress: Callable[[float, str], None] | None = None) -> int:
        if n < 0:
            raise ValueError("Number of steps must be non-negative")
        target = min(self.steps, self.step + n)
        total = max(1, target - self.step)
        done = 0
        while self.step < target:
            self.session.advance(self.planner.plan(self.session, self.goal))
            done += 1
            if progress and (done % 8 == 0 or self.step == target):
                progress(done / total, f"step {self.step}/{self.steps}")
        return done

    def record(self) -> dict:
        s = self.session.summary()
        return {
            "id": self.id, "owner": self.owner, "label": self.label,
            "scenario_id": self.session.initial_scenario["meta"]["id"],
            "scenario_title": self.session.initial_scenario["meta"]["title"],
            "scenario_hash": self.scenario_hash,
            "planner": self.planner_name, "params": self.planner_params, "goal": self.goal,
            "goal_history": self.goal_history, "parent": self.parent,
            "created_at": self.created_at, "updated_at": time.time(),
            "steps_executed": self.step, "steps": self.steps,
            "brief": {k: s[k] for k in ("jobs_completed", "jobs_total", "jobs_due_missed",
                                        "critical_jobs_due", "critical_jobs_completed_on_time",
                                        "revenue_usd")},
            "events": self.session.events, "commands": self.session.commands,
        }


class RunStore:
    def __init__(self, storage: Storage, capacity: int = MAX_RUNS_IN_MEMORY):
        self.storage = storage
        self.capacity = capacity
        self._runs: OrderedDict[str, Run] = OrderedDict()
        self._lock = threading.Lock()

    # ---- memory cache ----
    def _remember(self, run: Run) -> Run:
        with self._lock:
            self._runs[run.id] = run
            self._runs.move_to_end(run.id)
            while len(self._runs) > self.capacity:
                self._runs.popitem(last=False)  # already persisted
        return run

    def save(self, run: Run) -> None:
        self.storage.put_run(run.record())

    def _new_id(self) -> str:
        return uuid.uuid4().hex[:12]

    # ---- creation ----
    def create(self, owner: str, scenario: dict, goal: str, planner: str | None = None,
               params: dict | None = None, label: str = "") -> Run:
        check_goal(goal)
        session = Session(scenario)  # validates the scenario
        run = Run(id=self._new_id(), owner=owner, session=session,
                  planner=planner or DEFAULT_PLANNER, params=params or {}, goal=goal,
                  scenario_hash=self.storage.put_scenario(scenario), label=label)
        self.save(run)
        return self._remember(run)

    def fork(self, src: Run, goal: str | None = None, planner: str | None = None,
             params: dict | None = None, label: str = "", persist: bool = True) -> Run:
        """The caller must hold src.lock (or otherwise guarantee src is not advancing)."""
        same_planner = planner in (None, src.planner_name) and params is None
        run = Run(
            id=self._new_id(), owner=src.owner, session=src.session.fork(),
            planner=planner or src.planner_name,
            params=src.planner_params if params is None else params,
            goal=goal or src.goal, scenario_hash=src.scenario_hash, label=label,
            goal_history=copy.deepcopy(src.goal_history),
            parent={"run_id": src.id, "step": src.step},
            planner_obj=copy.deepcopy(src.planner) if same_planner else None,
        )
        if goal and goal != src.goal:
            run.goal_history.append({"step": run.step, "goal": goal})
            run.sync_metadata()
        if persist:
            self.save(run)
            self._remember(run)
        return run

    def restore_from_result(self, owner: str, record: dict, label: str = "") -> tuple[Run, dict]:
        """Rebuild a run from an exported result file and verify its summary."""
        if not isinstance(record, dict) or record.get("schema_version") != RESULT_SCHEMA:
            raise ValueError(f"Expected schema_version {RESULT_SCHEMA}")
        for key in ("initial_scenario", "events", "commands", "steps_executed"):
            if key not in record:
                raise ValueError(f"Result is missing '{key}'")
        scenario = record["initial_scenario"]
        session = replay_episode(scenario, record["events"], record["commands"],
                                 record["steps_executed"])
        md = record.get("run_metadata") or {}
        planner = md.get("algorithm") if md.get("algorithm") in PLANNERS else DEFAULT_PLANNER
        params = md.get("parameters") if planner == md.get("algorithm") else {}
        goal = md.get("goal") if md.get("goal") in GOALS else "priority"
        run = Run(id=self._new_id(), owner=owner, session=session, planner=planner,
                  params=params or {}, goal=goal, label=label or "imported",
                  scenario_hash=self.storage.put_scenario(scenario),
                  goal_history=copy.deepcopy(md.get("goal_switches") or []),
                  parent={"imported_from": md.get("run_id"), "step": record["steps_executed"]})
        restore(run.planner, run.session)
        check = {
            "scenario_hash_matches": record.get("initial_scenario_hash") in (None, digest(scenario)),
            "summary_matches": record.get("summary") is None or record["summary"] == session.summary(),
            "planner_substituted": planner != md.get("algorithm"),
        }
        self.save(run)
        self._remember(run)
        return run, check

    # ---- lookup ----
    def get(self, owner: str, run_id: str) -> Run:
        run = self._runs.get(run_id)
        if run is not None:
            if run.owner != owner:
                raise KeyError(f"Run {run_id} not found")
            with self._lock:
                self._runs.move_to_end(run_id)
            return run
        rec = self.storage.get_run(owner, run_id)
        if rec is None:
            raise KeyError(f"Run {run_id} not found")
        return self._remember(self._restore(rec))

    def _restore(self, rec: dict) -> Run:
        scenario = self.storage.get_scenario(rec["scenario_hash"])
        session = replay_episode(scenario, rec["events"], rec["commands"], rec["steps_executed"])
        run = Run(id=rec["id"], owner=rec["owner"], session=session, planner=rec["planner"],
                  params=rec["params"], goal=rec["goal"], scenario_hash=rec["scenario_hash"],
                  label=rec.get("label", ""), goal_history=rec.get("goal_history") or [],
                  parent=rec.get("parent"), created_at=rec.get("created_at"))
        restore(run.planner, run.session)
        return run

    def list(self, owner: str) -> list[dict]:
        return self.storage.list_runs(owner)

    def delete(self, owner: str, run_id: str) -> None:
        self.get(owner, run_id)
        with self._lock:
            self._runs.pop(run_id, None)
        self.storage.delete_run(owner, run_id)
