from model.operations import Session
from model.resource_env import load

from backend.app.config import DATA_DIR
from backend.app.planners import make_planner
from backend.tests.helpers import DEMO_EVENTS

FAST = {"horizon": 24, "replan_every": 8, "time_limit": 0.5}


def run(scenario_id, goal="priority", until=None, events=(), **params):
    s = load(DATA_DIR / f"{scenario_id}.json")
    session = Session(s)
    planner = make_planner("cpsat-mpc", {**FAST, **params})
    stop = until if until is not None else s["time"]["steps"]
    by_step = {}
    for e in events:
        by_step.setdefault(e["at_step"], []).append(e)
    while session.env.k < stop:
        for e in by_step.get(session.env.k, []):
            session.apply_event(e)
        session.advance(planner.plan(session, goal))
    return session, planner


def test_reaches_upper_bound_on_intro_without_waste():
    session, planner = run("P01_intro")
    s = session.summary()
    assert s["jobs_completed"] == 11  # LP upper bound for P01
    assert s["blocked_command_count"] == 0
    assert s["work_steps_in_missed_jobs"] == 0
    assert all(x["status"] in ("OPTIMAL", "FEASIBLE") for x in planner.solves)


def test_deterministic():
    a, _ = run("P02_shift", until=40)
    b, _ = run("P02_shift", until=40)
    assert a.commands == b.commands


def test_fork_continues_identically():
    s = load(DATA_DIR / "P02_shift.json")
    session = Session(s)
    planner = make_planner("cpsat-mpc", FAST)
    while session.env.k < 20:
        session.advance(planner.plan(session, "priority"))
    import copy
    s2, p2 = session.fork(), copy.deepcopy(planner)
    for sess, pl in ((session, planner), (s2, p2)):
        while sess.env.k < 36:
            sess.advance(pl.plan(sess, "priority"))
    assert session.commands == s2.commands


def test_replans_on_events_and_never_breaks_rules():
    session, planner = run("P02_shift", until=90, events=[e for e in DEMO_EVENTS if e["at_step"] < 90])
    assert any(x["reason"] == "event" for x in planner.solves)
    assert session.summary()["blocked_command_count"] == 0
    assert session.summary()["brownout_satellite_steps"] == 0


def test_energy_scenario_keeps_reserve_when_acting():
    session, _ = run("P03_energy", until=60)
    reserve = {v["id"]: v["capacity_wh"] * 0.3 for v in session.initial_scenario["satellites"]}
    for r in session.env.trace:
        if r["executed"] != "idle":
            assert r["energy_before_wh"] >= reserve[r["satellite_id"]] - 1e-6
            assert r["energy_after_wh"] >= reserve[r["satellite_id"]] - 1e-6


def run_planner(name, scenario_id, goal="priority", until=None, params=None):
    s = load(DATA_DIR / f"{scenario_id}.json")
    session = Session(s)
    planner = make_planner(name, params or {})
    stop = until if until is not None else s["time"]["steps"]
    while session.env.k < stop:
        session.advance(planner.plan(session, goal))
    return session, planner


def test_reservation_wastes_no_work_and_keeps_rules():
    session, _ = run_planner("reserve", "P04_demand", until=120)
    s = session.summary()
    assert s["blocked_command_count"] == 0 and s["brownout_satellite_steps"] == 0
    started = [j for j in session.env.jobs.values() if j["remaining_steps"] < j["work_steps"]]
    missed = [j for j in started if j["completed_step"] is None and j["deadline_step"] <= session.env.k]
    assert not missed  # all-or-nothing: nothing started is left unfinished past its deadline


def test_reservation_beats_greedy_on_high_load():
    greedy, _ = run_planner("greedy-edf", "P04_demand", until=96)
    reserve, _ = run_planner("reserve", "P04_demand", until=96)
    g, r = greedy.summary(), reserve.summary()
    assert r["critical_jobs_completed_on_time"] >= g["critical_jobs_completed_on_time"]
    assert r["revenue_usd"] > g["revenue_usd"]


def test_reservation_plan_is_feasible_for_the_exact_model():
    # The seed must satisfy the CP-SAT model exactly, otherwise the warm start is useless.
    from ortools.sat.python import cp_model
    from backend.app.planners.optimizer import OptimizerPlanner
    s = load(DATA_DIR / "P03_energy.json")
    session = Session(s)
    orig = cp_model.CpSolver.Solve

    def fixed(self, m, *a, **k):
        self.parameters.fix_variables_to_their_hinted_value = True
        return orig(self, m, *a, **k)

    cp_model.CpSolver.Solve = fixed
    try:
        p = OptimizerPlanner(horizon=36)
        p._solve(session.env, "priority", "test")
    finally:
        cp_model.CpSolver.Solve = orig
    assert p.solves[-1]["status"] == "OPTIMAL"


def test_energy_model_is_a_lower_bound_of_the_real_charge():
    from backend.app.planners.optimizer_common import UNITS_PER_WH, EnergyModel
    session, _ = run_planner("reserve", "P03_energy", until=40)
    for sid in ("S01", "S05", "S20"):
        rows = [r for r in session.env.trace if r["satellite_id"] == sid]
        fresh = Session(session.initial_scenario)
        model = EnergyModel(fresh.env, sid, 0, 40)
        for r in rows:
            if r["executed"] != "idle":
                power = r["load_w"] - 18 - r["heater_w"]
                model.power[r["step"]] = power
        model.E = [model.E[0]]
        for i in range(40):
            model.E.append(model._next(i, model.E[i], model.power[i]))
        for r in rows:
            assert model.E[r["step"] + 1] / UNITS_PER_WH <= r["energy_after_wh"] + 1e-6
