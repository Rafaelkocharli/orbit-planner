"""Planner interface. A planner picks actions for the current step only.

It sees what Session exposes (current state + known jobs/environment), never future events.
"""
from __future__ import annotations
from typing import Protocol

GOALS = ("priority", "revenue")


class Planner(Protocol):
    name: str
    version: str

    def params(self) -> dict: ...

    def plan(self, session, goal: str) -> dict[str, dict]:
        """Return {satellite_id: {"action": ...}} for session.env.k."""
        ...
