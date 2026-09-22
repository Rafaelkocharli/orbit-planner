"""HTTP API. Run from the repo root: uvicorn backend.app.main:app --reload

Clients identify themselves with the X-Client-Id header (any 1-64 chars of [A-Za-z0-9_-]);
runs and saved scenarios of different clients are isolated. Without the header the
client is "public". Heavy operations accept "background": true and return a task.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Callable

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import analysis, whatif
from .config import DEFAULT_CLIENT, ROOT
from .planners import GOALS, PLANNERS
from .report import render
from .runs import Run, RunStore
from .scenarios import ScenarioCatalog, apply_overrides
from .storage import Storage, safe_id
from .tasks import TaskManager

app = FastAPI(title="Orbit Planner", version="0.2.0")
storage = Storage()
catalog = ScenarioCatalog(storage)
store = RunStore(storage)
tasks = TaskManager()

READ_WAIT_S = 60


# ---------- request bodies ----------

class CreateRun(BaseModel):
    scenario_id: str | None = None
    scenario: dict | None = None
    overrides: dict | None = None
    goal: str = "priority"
    planner: str | None = None
    params: dict | None = None
    label: str = ""


class SaveScenario(BaseModel):
    base_id: str | None = None
    scenario: dict | None = None
    overrides: dict | None = None
    title: str | None = None


class Advance(BaseModel):
    steps: int | None = Field(None, ge=0)
    until_step: int | None = Field(None, ge=0)
    background: bool = False


class Goal(BaseModel):
    goal: str


class Fork(BaseModel):
    goal: str | None = None
    planner: str | None = None
    params: dict | None = None
    label: str = ""


class Compare(BaseModel):
    a: str
    b: str
    goal: str | None = None


class Strategies(BaseModel):
    candidates: list[dict] | None = None
    rank_goal: str | None = None
    background: bool = False


class WhatIfEvent(BaseModel):
    event: dict
    background: bool = False


class WhatIfResources(BaseModel):
    variations: list[dict]
    background: bool = False


class Background(BaseModel):
    background: bool = False


# ---------- helpers ----------

def client(x_client_id: str | None = Header(default=None)) -> str:
    if x_client_id is None or x_client_id == "":
        return DEFAULT_CLIENT
    try:
        return safe_id(x_client_id, "X-Client-Id")
    except ValueError as e:
        raise HTTPException(400, str(e))


def get_run(run_id: str, owner: str) -> Run:
    try:
        return store.get(owner, run_id)
    except KeyError as e:
        raise HTTPException(404, str(e.args[0]))
    except ValueError as e:
        raise HTTPException(400, str(e))


@contextmanager
def mutating(run: Run):
    """Mutations never wait: a busy run answers 409 instead of queueing changes."""
    if not run.lock.acquire(blocking=False):
        raise HTTPException(409, "Запуск занят: идёт расчёт. Дождитесь завершения задачи.")
    try:
        yield
    finally:
        run.lock.release()


@contextmanager
def reading(run: Run):
    if not run.lock.acquire(timeout=READ_WAIT_S):
        raise HTTPException(409, "Запуск занят: идёт расчёт.")
    try:
        yield
    finally:
        run.lock.release()


def bad_request(fn: Callable[[], Any]):
    try:
        return fn()
    except HTTPException:
        raise
    except (ValueError, KeyError, TypeError) as e:
        raise HTTPException(400, str(e.args[0]) if e.args else str(e))


def maybe_background(owner: str, kind: str, background: bool, fn: Callable[[Callable], dict]):
    if background:
        return JSONResponse(tasks.submit(owner, kind, fn), status_code=202)
    return bad_request(lambda: fn(None))


def state(run: Run) -> dict:
    s = run.session
    return {
        "run_id": run.id,
        "label": run.label,
        "scenario_id": s.initial_scenario["meta"]["id"],
        "scenario_title": s.initial_scenario["meta"]["title"],
        "goal": run.goal,
        "goal_history": run.goal_history,
        "planner": run.planner_name,
        "params": run.planner_params,
        "run_metadata": s.run_metadata,
        "step": run.step,
        "steps": run.steps,
        "step_s": s.env.s["time"]["step_s"],
        "finished": run.finished,
        "summary": s.summary(),
        "satellites": {
            sid: {"energy_wh": round(st["energy_wh"], 6),
                  "soc_pct": round(100 * st["energy_wh"] / s.env.sats[sid]["capacity_wh"], 6),
                  "temp_c": round(st["temp_c"], 6),
                  "calibration_age_steps": st["calibration_age_steps"],
                  "calibration_left_steps": max(0, s.env.s["model"]["calibration_valid_steps"]
                                                - st["calibration_age_steps"]),
                  "available": s.env.available(sid)}
            for sid, st in s.env.state.items()
        },
        "events": s.events,
        "parent": run.parent,
    }


# ---------- catalog ----------

@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/meta")
def meta(owner: str = Depends(client)):
    return {"goals": list(GOALS), "planners": {n: p.version for n, p in PLANNERS.items()},
            "event_types": ["add_jobs", "satellite_outage", "close_downlink"],
            "override_keys": ["initial_soc", "solar_factor", "job_priority", "failures",
                              "downlink_parallel_limit"],
            "scenarios": catalog.list(owner)}


@app.get("/api/scenarios")
def scenarios(owner: str = Depends(client)):
    return catalog.list(owner)


@app.get("/api/scenarios/{scenario_id}")
def scenario(scenario_id: str, full: bool = False, owner: str = Depends(client)):
    s = bad_request(lambda: catalog.get(owner, scenario_id))
    if full:
        return s
    return {"meta": s["meta"], "time": s["time"], "model": s["model"],
            "satellites": s["satellites"], "failures": s["failures"],
            "jobs": {"total": len(s["jobs"]),
                     "by_kind": {k: sum(j["kind"] == k for j in s["jobs"]) for k in ("downlink", "relay")},
                     "by_priority": {p: sum(j["priority"] == p for j in s["jobs"]) for p in (1, 2, 3)}}}


@app.post("/api/scenarios")
def save_scenario(body: SaveScenario, owner: str = Depends(client)):
    """Upload a scenario or save a modified variant of an existing one."""
    def run():
        base = body.scenario if body.scenario is not None else catalog.get(owner, body.base_id or "")
        return catalog.save(owner, apply_overrides(base, body.overrides), body.title,
                            base_id=body.base_id, overrides=body.overrides)
    return bad_request(run)


# ---------- runs ----------

@app.post("/api/runs")
def create_run(body: CreateRun, owner: str = Depends(client)):
    def run():
        base = body.scenario if body.scenario is not None else catalog.get(owner, body.scenario_id or "")
        scenario = apply_overrides(base, body.overrides)
        return state(store.create(owner, scenario, body.goal, body.planner, body.params, body.label))
    return bad_request(run)


@app.post("/api/runs/import")
def import_run(record: dict, owner: str = Depends(client)):
    """Load an exported result (cosmo-B-ops-result-1.0), replay it and continue from there."""
    def run():
        r, check = store.restore_from_result(owner, record)
        return {**state(r), "import_check": check}
    return bad_request(run)


@app.get("/api/runs")
def list_runs(owner: str = Depends(client)):
    return store.list(owner)


@app.get("/api/runs/{run_id}")
def get_run_state(run_id: str, owner: str = Depends(client)):
    run = get_run(run_id, owner)
    with reading(run):
        return state(run)


@app.delete("/api/runs/{run_id}")
def delete_run(run_id: str, owner: str = Depends(client)):
    run = get_run(run_id, owner)
    with mutating(run):
        store.delete(owner, run_id)
    return {"deleted": run_id}


@app.post("/api/runs/{run_id}/advance")
def advance(run_id: str, body: Advance, owner: str = Depends(client)):
    run = get_run(run_id, owner)
    if body.steps is not None:
        n = body.steps
    else:
        target = run.steps if body.until_step is None else body.until_step
        if target > run.steps:
            raise HTTPException(400, f"until_step must be <= {run.steps}")
        if target < run.step:
            raise HTTPException(400, f"Step {target} is already executed (current {run.step})")
        n = target - run.step
    if not run.lock.acquire(blocking=False):
        raise HTTPException(409, "Запуск занят: идёт расчёт.")

    def work(progress):
        try:
            done = run.advance(n, progress)
            store.save(run)
            return {"steps_done": done, **state(run)}
        finally:
            run.lock.release()

    if body.background:
        return JSONResponse(tasks.submit(owner, "advance", work), status_code=202)
    return bad_request(lambda: work(None))


@app.post("/api/runs/{run_id}/events")
def add_event(run_id: str, event: dict, owner: str = Depends(client)):
    """One event object. If id/at_step are omitted (manual input), they are filled in."""
    run = get_run(run_id, owner)
    with mutating(run):
        ev = dict(event)
        ev.setdefault("at_step", run.step)
        if "id" not in ev:
            ev["id"] = f"OP-{run.step:03d}-{len(run.session.events) + 1:02d}"
        try:
            run.session.apply_event(ev)
        except (ValueError, KeyError, TypeError) as e:
            raise HTTPException(400, f"Сообщение отклонено, состояние не изменилось: {e}")
        store.save(run)
        return state(run)


@app.post("/api/runs/{run_id}/goal")
def set_goal(run_id: str, body: Goal, owner: str = Depends(client)):
    run = get_run(run_id, owner)
    with mutating(run):
        bad_request(lambda: run.set_goal(body.goal))
        store.save(run)
        return state(run)


@app.post("/api/runs/{run_id}/fork")
def fork(run_id: str, body: Fork, owner: str = Depends(client)):
    run = get_run(run_id, owner)
    with mutating(run):
        return bad_request(lambda: state(store.fork(run, body.goal, body.planner, body.params, body.label)))


# ---------- views for the UI ----------

@app.get("/api/runs/{run_id}/jobs")
def jobs(run_id: str, status: str | None = None, priority: int | None = None,
         satellite_id: str | None = None, kind: str | None = None, owner: str = Depends(client)):
    run = get_run(run_id, owner)
    with reading(run):
        rows = analysis.jobs_report(run.session.env)
    return [r for r in rows
            if (status is None or r["status"] == status)
            and (priority is None or r["priority"] == priority)
            and (kind is None or r["kind"] == kind)
            and (satellite_id is None or satellite_id in r["eligible_satellites"])]


@app.get("/api/runs/{run_id}/trace")
def trace(run_id: str, satellite_id: str | None = None, from_step: int = 0, to_step: int | None = None,
          owner: str = Depends(client)):
    run = get_run(run_id, owner)
    with reading(run):
        rows = list(run.session.env.trace)
    return [r for r in rows if (satellite_id is None or r["satellite_id"] == satellite_id)
            and r["step"] >= from_step and (to_step is None or r["step"] < to_step)]


@app.get("/api/runs/{run_id}/schedule")
def schedule(run_id: str, from_step: int = 0, to_step: int | None = None, owner: str = Depends(client)):
    """Compact executed schedule for a Gantt chart: per satellite, runs of identical actions."""
    run = get_run(run_id, owner)
    with reading(run):
        rows = list(run.session.env.trace)
        jobs_meta = {j: {"kind": v["kind"], "priority": v["priority"]} for j, v in run.session.env.jobs.items()}
    lanes: dict[str, list] = {}
    for r in rows:
        if r["step"] < from_step or (to_step is not None and r["step"] >= to_step):
            continue
        label = r["requested"].get("job_id") if r["executed"] == "job" else r["executed"]
        blocked = r["reason"] not in analysis.NOT_BLOCKED
        lane = lanes.setdefault(r["satellite_id"], [])
        if lane and lane[-1]["action"] == r["executed"] and lane[-1]["label"] == label \
                and lane[-1]["end_step"] == r["step"] and not blocked and not lane[-1]["blocked"]:
            lane[-1]["end_step"] += 1
        else:
            item = {"start_step": r["step"], "end_step": r["step"] + 1, "action": r["executed"],
                    "label": label, "blocked": blocked, "reason": r["reason"] if blocked else None}
            if r["executed"] == "job":
                item.update(jobs_meta[label])
            lane.append(item)
    return {"from_step": from_step, "lanes": lanes}


@app.get("/api/runs/{run_id}/satellites/{sid}/series")
def series(run_id: str, sid: str, owner: str = Depends(client)):
    """Time series for charts: charge, temperature, calibration age, action per step."""
    run = get_run(run_id, owner)
    with reading(run):
        env = run.session.env
        if sid not in env.sats:
            raise HTTPException(404, f"Unknown satellite {sid}")
        sat = next(v for v in run.session.initial_scenario["satellites"] if v["id"] == sid)
        rows = [r for r in env.trace if r["satellite_id"] == sid]
        cap, m = env.sats[sid]["capacity_wh"], env.s["model"]
        e = env.s["environment"][sid]
        return {
            "satellite_id": sid, "capacity_wh": cap,
            "limits": {"reserve_soc_pct": m["reserve_soc_pct"], "critical_soc_pct": m["critical_soc_pct"],
                       "payload_min_c": m["payload_min_c"], "payload_max_c": m["payload_max_c"],
                       "calibration_valid_steps": m["calibration_valid_steps"]},
            "step": [0] + [r["step"] + 1 for r in rows],
            "soc_pct": [sat["initial_soc_pct"]] + [round(100 * r["energy_after_wh"] / cap, 6) for r in rows],
            "temp_c": [sat["initial_temp_c"]] + [r["temp_after_c"] for r in rows],
            "calibration_age_steps": [sat["initial_calibration_age_steps"]] + [r["calibration_age_steps"] for r in rows],
            "action": [r["executed"] for r in rows],
            "job_id": [r["requested"].get("job_id") if r["executed"] == "job" else None for r in rows],
            "environment": {"solar_w": e["solar_w"], "downlink_available": e["downlink_available"],
                            "relay_available": e["relay_available"], "thermal_target_c": e["thermal_target_c"]},
        }


# ---------- analysis ----------

@app.get("/api/runs/{run_id}/analytics")
def run_analytics(run_id: str, owner: str = Depends(client)):
    run = get_run(run_id, owner)
    with reading(run):
        return analysis.analytics(run.session)


@app.get("/api/runs/{run_id}/missed")
def missed(run_id: str, owner: str = Depends(client)):
    run = get_run(run_id, owner)
    with reading(run):
        return analysis.missed_jobs(run.session)


@app.get("/api/runs/{run_id}/jobs/{job_id}/why")
def why_job(run_id: str, job_id: str, owner: str = Depends(client)):
    run = get_run(run_id, owner)
    with reading(run):
        job = run.session.env.jobs.get(job_id)
        if job is None:
            raise HTTPException(404, f"Unknown job {job_id}")
        out = {"job": {k: job[k] for k in job}, **analysis.job_outcome(run.session.env, job)}
        if out["status"] == "missed":
            out["cause"] = analysis.miss_causes(run.session, job)
        elif out["status"] == "infeasible":
            out["proof"] = analysis.infeasibility_proof(run.session.env, job)
        return out


@app.get("/api/runs/{run_id}/explain")
def explain(run_id: str, step: int = Query(..., ge=0), satellite_id: str = Query(...),
            owner: str = Depends(client)):
    run = get_run(run_id, owner)
    with reading(run):
        return bad_request(lambda: analysis.explain_decision(run.session, step, satellite_id))


@app.get("/api/runs/{run_id}/planner")
def planner_report(run_id: str, owner: str = Depends(client)):
    """How the planner decided: replanning log with status and optimality gap."""
    run = get_run(run_id, owner)
    with reading(run):
        hook = getattr(run.planner, "report", None)
        return {"planner": run.planner_name, "version": run.planner.version, "params": run.planner.params(),
                "report": hook() if callable(hook) else None}


@app.post("/api/runs/{run_id}/forecast")
def forecast(run_id: str, body: Background, owner: str = Depends(client)):
    run = get_run(run_id, owner)
    return maybe_background(owner, "forecast", body.background, lambda p: whatif.forecast(run, p))


@app.post("/api/compare")
def compare(body: Compare, owner: str = Depends(client)):
    a, b = get_run(body.a, owner), get_run(body.b, owner)
    if a is b:
        raise HTTPException(400, "Choose two different runs")
    with reading(a), reading(b):
        return bad_request(lambda: whatif.compare(a, b, body.goal))


@app.post("/api/runs/{run_id}/strategies")
def strategies(run_id: str, body: Strategies, owner: str = Depends(client)):
    """Continue from the current state with several strategies; each becomes a saved branch."""
    run = get_run(run_id, owner)
    cands = body.candidates or [{"planner": p, "goal": g} for p in PLANNERS for g in GOALS]
    return maybe_background(owner, "strategies", body.background,
                            lambda p: whatif.strategies(store, run, cands, body.rank_goal, p))


@app.post("/api/runs/{run_id}/what-if/event")
def whatif_event(run_id: str, body: WhatIfEvent, owner: str = Depends(client)):
    run = get_run(run_id, owner)
    return maybe_background(owner, "what-if-event", body.background,
                            lambda p: whatif.whatif_event(run, body.event, p))


@app.post("/api/runs/{run_id}/what-if/resources")
def whatif_resources(run_id: str, body: WhatIfResources, owner: str = Depends(client)):
    run = get_run(run_id, owner)
    return maybe_background(owner, "what-if-resources", body.background,
                            lambda p: whatif.whatif_resources(run, body.variations, p))


@app.get("/api/tasks/{task_id}")
def task(task_id: str, owner: str = Depends(client)):
    try:
        return tasks.get(owner, task_id)
    except KeyError as e:
        raise HTTPException(404, str(e.args[0]))


# ---------- export ----------

@app.get("/api/runs/{run_id}/result")
def result(run_id: str, owner: str = Depends(client)):
    run = get_run(run_id, owner)
    with reading(run):
        data = run.session.result()
    return JSONResponse(data, headers={"Content-Disposition": f'attachment; filename="result-{run.id}.json"'})


@app.get("/api/runs/{run_id}/report", response_class=HTMLResponse)
def report(run_id: str, owner: str = Depends(client)):
    run = get_run(run_id, owner)
    with reading(run):
        return HTMLResponse(render(run))


dist = ROOT / "frontend" / "dist"
if dist.exists():
    app.mount("/", StaticFiles(directory=dist, html=True), name="ui")
