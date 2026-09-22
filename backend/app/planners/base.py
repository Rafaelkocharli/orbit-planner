"""Planner interface. A planner picks actions for the current step only.

It sees what Session exposes (current state, known jobs and environment, received
events) and never future events. The planner instance is deep-copied on fork, so
internal state follows its branch. After a restore from disk a fresh instance is
created; a stateful planner must rebuild its state from the session in `restore`.
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


def restore(planner, session) -> None:
    """Optional hook: let a stateful planner rebuild its state after replay."""
    hook = getattr(planner, "restore", None)
    if callable(hook):
        hook(session)
