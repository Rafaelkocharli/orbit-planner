from .base import GOALS, Planner
from .greedy import GreedyPlanner

# Register new planners here; the API and UI read this registry.
PLANNERS = {
    GreedyPlanner.name: GreedyPlanner,
}


def make_planner(name: str, params: dict | None = None) -> Planner:
    if name not in PLANNERS:
        raise ValueError(f"Unknown planner: {name}")
    return PLANNERS[name](**(params or {}))
