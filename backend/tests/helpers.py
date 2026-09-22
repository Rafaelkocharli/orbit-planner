import json
import time

from fastapi.testclient import TestClient

from backend.app.config import EXAMPLES_DIR
from backend.app.main import app

client = TestClient(app)
DEMO_EVENTS = json.loads((EXAMPLES_DIR / "events_demo.json").read_text())["events"]


def h(cid="alice"):
    return {"X-Client-Id": cid}


def new_run(scenario_id="P01_intro", cid="alice", **kw):
    r = client.post("/api/runs", json={"scenario_id": scenario_id, **kw}, headers=h(cid))
    assert r.status_code == 200, r.text
    return r.json()["run_id"]


def post(path, body=None, cid="alice"):
    return client.post(path, json=body if body is not None else {}, headers=h(cid))


def get(path, cid="alice", **params):
    return client.get(path, headers=h(cid), params=params)


def wait_task(tid, cid="alice", timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        t = get(f"/api/tasks/{tid}", cid).json()
        if t["status"] in ("done", "failed"):
            return t
        time.sleep(0.05)
    raise AssertionError("task timeout")


def run_with_demo_events(until=None):
    rid = new_run("P02_shift")
    for e in DEMO_EVENTS:
        if until is not None and e["at_step"] > until:
            break
        post(f"/api/runs/{rid}/advance", {"until_step": e["at_step"]})
        assert post(f"/api/runs/{rid}/events", e).status_code == 200
    post(f"/api/runs/{rid}/advance", {} if until is None else {"until_step": until})
    return rid
