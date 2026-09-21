"""HTTP API. Run from repo root: uvicorn backend.app.main:app --reload"""
from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .analysis import jobs_report
from .config import ROOT
from .planners import GOALS, PLANNERS
from .runs import Run, RunStore
from .scenarios import apply_overrides, list_scenarios, load_scenario

app = FastAPI(title="Orbit Planner")
store = RunStore()


class CreateRun(BaseModel):
    scenario_id: str | None = None
    scenario: dict | None = None
    goal: str = "priority"
    planner: str = "greedy-edf"
    params: dict | None = None
    overrides: dict | None = None


class Advance(BaseModel):
    steps: int | None = None
    until_step: int | None = None


class Fork(BaseModel):
    goal: str | None = None
    planner: str | None = None
    params: dict | None = None


class Goal(BaseModel):
    goal: str


def _get(run_id: str) -> Run:
    try:
        return store.get(run_id)
    except KeyError as e:
        raise HTTPException(404, str(e))


def _state(run: Run) -> dict:
    s = run.session
    return {
        "run_id": run.id,
        "scenario_id": s.initial_scenario["meta"]["id"],
        "goal": run.goal,
        "planner": run.planner_name,
        "step": s.env.k,
        "steps": run.steps,
        "finished": s.env.k >= run.steps,
        "summary": s.summary(),
        "satellites": {
            sid: {**st, "soc_pct": 100 * st["energy_wh"] / s.env.sats[sid]["capacity_wh"],
                  "available": s.env.available(sid)}
            for sid, st in s.env.state.items()
        },
        "events": s.events,
        "parent": run.parent,
    }


@app.get("/api/meta")
def meta():
    return {"goals": GOALS, "planners": list(PLANNERS), "scenarios": list_scenarios()}


@app.post("/api/runs")
def create_run(body: CreateRun):
    try:
        scenario = body.scenario or load_scenario(body.scenario_id or "")
        scenario = apply_overrides(scenario, body.overrides)
        run = store.create(scenario, body.goal, body.planner, body.params)
    except (ValueError, KeyError, TypeError) as e:
        raise HTTPException(400, f"Scenario rejected: {e}")
    return _state(run)


@app.get("/api/runs/{run_id}")
def get_run(run_id: str):
    return _state(_get(run_id))


@app.post("/api/runs/{run_id}/advance")
def advance(run_id: str, body: Advance):
    run = _get(run_id)
    k = run.session.env.k
    n = body.steps if body.steps is not None else (body.until_step or run.steps) - k
    if n < 0:
        raise HTTPException(400, "until_step is in the past")
    with run.lock:
        run.advance(n)
    return _state(run)


@app.post("/api/runs/{run_id}/events")
def add_event(run_id: str, event: dict):
    run = _get(run_id)
    with run.lock:
        try:
            run.session.apply_event(event)
        except (ValueError, KeyError, TypeError) as e:
            raise HTTPException(400, f"Event rejected, state unchanged: {e}")
    return _state(run)


@app.post("/api/runs/{run_id}/goal")
def set_goal(run_id: str, body: Goal):
    run = _get(run_id)
    try:
        run.set_goal(body.goal)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _state(run)


@app.post("/api/runs/{run_id}/fork")
def fork(run_id: str, body: Fork):
    try:
        run = store.fork(_get(run_id), body.goal, body.planner, body.params)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _state(run)


@app.get("/api/runs/{run_id}/jobs")
def jobs(run_id: str):
    return jobs_report(_get(run_id).session.env)


@app.get("/api/runs/{run_id}/trace")
def trace(run_id: str, satellite_id: str | None = None):
    rows = _get(run_id).session.env.trace
    return [r for r in rows if satellite_id is None or r["satellite_id"] == satellite_id]


@app.get("/api/runs/{run_id}/result")
def result(run_id: str):
    return _get(run_id).session.result()


dist = ROOT / "frontend" / "dist"
if dist.exists():
    app.mount("/", StaticFiles(directory=dist, html=True), name="ui")
