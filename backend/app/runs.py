"""Run store: each run owns an independent Session, planner and history.

Runs never share mutable state; fork() deep-copies the session (T3).
"""
from __future__ import annotations

import copy
import threading
import uuid
from dataclasses import dataclass, field

from model.operations import Session

from .config import ALGORITHM_VERSION, MAX_RUNS
from .planners import GOALS, make_planner


@dataclass
class Run:
    id: str
    session: Session
    planner_name: str
    planner_params: dict
    goal: str
    goal_history: list = field(default_factory=list)
    parent: dict | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)

    def __post_init__(self):
        self.planner = make_planner(self.planner_name, self.planner_params)
        self._sync_metadata()

    def _sync_metadata(self):
        md = {
            "goal": self.goal,
            "algorithm": self.planner.name,
            "version": f"{ALGORITHM_VERSION}/{self.planner.version}",
            "parameters": self.planner.params(),
            "goal_switches": self.goal_history,
        }
        if self.parent:
            md["forked_from"] = self.parent
        self.session.run_metadata = md

    @property
    def steps(self) -> int:
        return self.session.env.s["time"]["steps"]

    def set_goal(self, goal: str):
        check_goal(goal)
        if goal != self.goal:
            self.goal_history.append({"step": self.session.env.k, "goal": goal})
            self.goal = goal
            self._sync_metadata()

    def advance(self, n: int) -> int:
        done = 0
        while done < n and self.session.env.k < self.steps:
            self.session.advance(self.planner.plan(self.session, self.goal))
            done += 1
        return done


def check_goal(goal: str):
    if goal not in GOALS:
        raise ValueError(f"goal must be one of {GOALS}")


class RunStore:
    def __init__(self):
        self._runs: dict[str, Run] = {}
        self._lock = threading.Lock()

    def _put(self, run: Run) -> Run:
        with self._lock:
            if len(self._runs) >= MAX_RUNS:
                self._runs.pop(next(iter(self._runs)))
            self._runs[run.id] = run
        return run

    def create(self, scenario: dict, goal: str, planner: str, params: dict | None) -> Run:
        check_goal(goal)
        return self._put(Run(uuid.uuid4().hex[:12], Session(scenario), planner, params or {}, goal))

    def fork(self, src: Run, goal: str | None, planner: str | None, params: dict | None) -> Run:
        run = Run(
            uuid.uuid4().hex[:12],
            src.session.fork(),
            planner or src.planner_name,
            params if params is not None else copy.deepcopy(src.planner_params),
            goal or src.goal,
            goal_history=copy.deepcopy(src.goal_history),
            parent={"run_id": src.id, "step": src.session.env.k},
        )
        check_goal(run.goal)
        return self._put(run)

    def get(self, run_id: str) -> Run:
        try:
            return self._runs[run_id]
        except KeyError:
            raise KeyError(f"Run {run_id} not found")
