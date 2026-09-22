from .base import GOALS, Planner, restore
from .greedy import GreedyPlanner
from .optimizer import OptimizerPlanner
from .reservation import ReservationPlanner

# Register new planners here; the API and UI read this registry.
# The first entry is the default; greedy-edf stays as the simple rule to compare against.
PLANNERS = {
    OptimizerPlanner.name: OptimizerPlanner,
    ReservationPlanner.name: ReservationPlanner,
    GreedyPlanner.name: GreedyPlanner,
}
DEFAULT_PLANNER = OptimizerPlanner.name


def make_planner(name: str, params: dict | None = None) -> Planner:
    if name not in PLANNERS:
        raise ValueError(f"Unknown planner: {name}. Available: {sorted(PLANNERS)}")
    try:
        return PLANNERS[name](**(params or {}))
    except TypeError as e:
        raise ValueError(f"Invalid parameters for {name}: {e}")
