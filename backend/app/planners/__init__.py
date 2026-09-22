from .base import GOALS, Planner, restore
from .greedy import GreedyPlanner
from .optimizer import OptimizerPlanner
from .reservation import ReservationPlanner

# Register new planners here; the API and UI read this registry.
# The first entry is the default: reserve runs a shift in seconds even on one CPU; cpsat-mpc is
# the maximum-result option (minutes on the server for P03); greedy-edf is the simple rule to
# compare against.
PLANNERS = {
    ReservationPlanner.name: ReservationPlanner,
    OptimizerPlanner.name: OptimizerPlanner,
    GreedyPlanner.name: GreedyPlanner,
}
DEFAULT_PLANNER = ReservationPlanner.name


def make_planner(name: str, params: dict | None = None) -> Planner:
    if name not in PLANNERS:
        raise ValueError(f"Unknown planner: {name}. Available: {sorted(PLANNERS)}")
    try:
        return PLANNERS[name](**(params or {}))
    except TypeError as e:
        raise ValueError(f"Invalid parameters for {name}: {e}")
