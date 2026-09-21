import json

from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.config import EXAMPLES_DIR
from model.operations import replay_episode

client = TestClient(app)


def new_run(scenario_id="P01_intro", **kw):
    r = client.post("/api/runs", json={"scenario_id": scenario_id, **kw})
    assert r.status_code == 200, r.text
    return r.json()["run_id"]


def test_full_run_replays_to_same_summary():
    rid = new_run()
    state = client.post(f"/api/runs/{rid}/advance", json={}).json()
    assert state["finished"]
    res = client.get(f"/api/runs/{rid}/result").json()
    replayed = replay_episode(res["initial_scenario"], res["events"], res["commands"])
    assert replayed.summary() == res["summary"]


def test_bad_event_rejected_state_kept():
    rid = new_run("P02_shift")
    client.post(f"/api/runs/{rid}/advance", json={"until_step": 72})
    before = client.get(f"/api/runs/{rid}").json()
    bad = {"id": "X", "at_step": 50, "type": "add_jobs", "jobs": []}
    assert client.post(f"/api/runs/{rid}/events", json=bad).status_code == 400
    assert client.get(f"/api/runs/{rid}").json() == before


def test_events_applied_once_and_replayable():
    events = json.loads((EXAMPLES_DIR / "events_demo.json").read_text())["events"]
    rid = new_run("P02_shift")
    for e in events:
        client.post(f"/api/runs/{rid}/advance", json={"until_step": e["at_step"]})
        assert client.post(f"/api/runs/{rid}/events", json=e).status_code == 200
    assert client.post(f"/api/runs/{rid}/events", json=events[-1]).status_code == 400
    client.post(f"/api/runs/{rid}/advance", json={})
    res = client.get(f"/api/runs/{rid}/result").json()
    assert replay_episode(res["initial_scenario"], res["events"], res["commands"]).summary() == res["summary"]


def test_fork_is_independent():
    rid = new_run("P02_shift")
    client.post(f"/api/runs/{rid}/advance", json={"until_step": 100})
    fid = client.post(f"/api/runs/{rid}/fork", json={"goal": "revenue"}).json()["run_id"]
    client.post(f"/api/runs/{fid}/advance", json={})
    parent = client.get(f"/api/runs/{rid}").json()
    assert parent["step"] == 100
    assert client.get(f"/api/runs/{fid}/result").json()["run_metadata"]["forked_from"] == {"run_id": rid, "step": 100}


def test_overrides_create_new_scenario():
    rid = new_run(overrides={"solar_factor": 0.5, "initial_soc": {"S01": 40}})
    assert client.get(f"/api/runs/{rid}").json()["scenario_id"] == "P01_intro-custom"
    assert client.post("/api/runs", json={"scenario_id": "P01_intro",
                                          "overrides": {"initial_soc": {"S99": 1}}}).status_code == 400
