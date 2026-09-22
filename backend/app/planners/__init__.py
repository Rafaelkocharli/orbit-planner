from .base import GOALS, Planner, restore
from .greedy import GreedyPlanner

# Register new planners here; the API and UI read this registry.
PLANNERS = {
    GreedyPlanner.name: GreedyPlanner,
}
DEFAULT_PLANNER = GreedyPlanner.name


def make_planner(name: str, params: dict | None = None) -> Planner:
    if name not in PLANNERS:
        raise ValueError(f"Unknown planner: {name}. Available: {sorted(PLANNERS)}")
    try:
        return PLANNERS[name](**(params or {}))
    except TypeError as e:
        raise ValueError(f"Invalid parameters for {name}: {e}")
