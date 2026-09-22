from model.operations import replay_episode

from backend.app.main import store
from backend.tests.helpers import DEMO_EVENTS, client, get, h, new_run, post, run_with_demo_events


def test_full_run_replays_to_same_summary():
    rid = new_run()
    assert post(f"/api/runs/{rid}/advance").json()["finished"]
    res = get(f"/api/runs/{rid}/result").json()
    assert res["schema_version"] == "cosmo-B-ops-result-1.0"
    assert res["run_metadata"]["goal"] == "priority" and res["run_metadata"]["algorithm"]
    replayed = replay_episode(res["initial_scenario"], res["events"], res["commands"])
    assert replayed.summary() == res["summary"]


def test_bad_event_rejected_state_kept():
    rid = new_run("P02_shift")
    post(f"/api/runs/{rid}/advance", {"until_step": 72})
    before = get(f"/api/runs/{rid}").json()
    for bad in ({"id": "X", "at_step": 50, "type": "add_jobs", "jobs": []},
                {"id": "Y", "at_step": 72, "type": "satellite_outage", "satellite_ids": ["S99"], "end_step": 80},
                {"id": "Z", "at_step": 72, "type": "boom"}):
        r = post(f"/api/runs/{rid}/events", bad)
        assert r.status_code == 400 and "состояние не изменилось" in r.json()["detail"]
    assert get(f"/api/runs/{rid}").json() == before


def test_events_applied_once_and_replayable():
    rid = run_with_demo_events()
    assert post(f"/api/runs/{rid}/events", DEMO_EVENTS[0]).status_code == 400  # past step
    res = get(f"/api/runs/{rid}/result").json()
    assert [e["id"] for e in res["events"]] == [e["id"] for e in DEMO_EVENTS]
    assert replay_episode(res["initial_scenario"], res["events"], res["commands"]).summary() == res["summary"]


def test_duplicate_event_id_rejected():
    rid = new_run("P02_shift")
    post(f"/api/runs/{rid}/advance", {"until_step": 72})
    assert post(f"/api/runs/{rid}/events", DEMO_EVENTS[0]).status_code == 200
    again = dict(DEMO_EVENTS[0], jobs=[dict(DEMO_EVENTS[0]["jobs"][0], id="OTHER")])
    assert post(f"/api/runs/{rid}/events", again).status_code == 400


def test_manual_event_gets_id_and_step():
    rid = new_run("P02_shift")
    post(f"/api/runs/{rid}/advance", {"until_step": 10})
    r = post(f"/api/runs/{rid}/events", {"type": "satellite_outage", "satellite_ids": ["S01"], "end_step": 20})
    assert r.status_code == 200
    ev = r.json()["events"][0]
    assert ev["at_step"] == 10 and ev["id"].startswith("OP-")
    assert r.json()["satellites"]["S01"]["available"] is False


def test_fork_is_independent_and_recorded():
    rid = new_run("P02_shift")
    post(f"/api/runs/{rid}/advance", {"until_step": 100})
    fid = post(f"/api/runs/{rid}/fork", {"goal": "revenue"}).json()["run_id"]
    post(f"/api/runs/{fid}/advance")
    assert get(f"/api/runs/{rid}").json()["step"] == 100
    md = get(f"/api/runs/{fid}/result").json()["run_metadata"]
    assert md["forked_from"] == {"run_id": rid, "step": 100}
    assert md["goal_switches"] == [{"step": 100, "goal": "revenue"}]


def test_goal_switch_is_recorded():
    rid = new_run()
    post(f"/api/runs/{rid}/advance", {"steps": 5})
    post(f"/api/runs/{rid}/goal", {"goal": "revenue"})
    assert get(f"/api/runs/{rid}/result").json()["run_metadata"]["goal_switches"] == [{"step": 5, "goal": "revenue"}]
    assert post(f"/api/runs/{rid}/goal", {"goal": "nope"}).status_code == 400


def test_advance_bounds():
    rid = new_run()
    post(f"/api/runs/{rid}/advance", {"until_step": 10})
    assert post(f"/api/runs/{rid}/advance", {"until_step": 5}).status_code == 400
    assert post(f"/api/runs/{rid}/advance", {"until_step": 999}).status_code == 400
    assert post(f"/api/runs/{rid}/advance", {"steps": 1000}).json()["step"] == 48


def test_overrides_and_saved_scenarios():
    rid = new_run(overrides={"solar_factor": 0.5, "initial_soc": {"*": 40}, "downlink_parallel_limit": 3})
    assert get(f"/api/runs/{rid}").json()["summary"]["minimum_soc_pct"] == 40
    assert post("/api/runs", {"scenario_id": "P01_intro", "overrides": {"initial_soc": {"S99": 1}}}).status_code == 400
    assert post("/api/runs", {"scenario_id": "P01_intro", "overrides": {"bogus": 1}}).status_code == 400
    saved = post("/api/scenarios", {"base_id": "P01_intro", "overrides": {"solar_factor": 0.4},
                                    "title": "Мало солнца"}).json()
    assert saved["id"].startswith("custom-")
    assert any(s["id"] == saved["id"] for s in get("/api/scenarios").json())
    assert all(s["id"] != saved["id"] for s in get("/api/scenarios", cid="bob").json())
    assert new_run(saved["id"])


def test_invalid_scenario_upload_is_explained():
    r = post("/api/scenarios", {"scenario": {"schema_version": "wrong"}})
    assert r.status_code == 400 and "schema" in r.json()["detail"]


def test_clients_are_isolated():
    rid = new_run(cid="alice")
    assert get(f"/api/runs/{rid}", cid="bob").status_code == 404
    assert all(r["id"] != rid for r in get("/api/runs", cid="bob").json())
    assert any(r["id"] == rid for r in get("/api/runs", cid="alice").json())
    assert get("/api/runs", cid="../etc").status_code == 400


def test_run_restored_from_disk_after_eviction():
    rid = run_with_demo_events(until=80)
    before = get(f"/api/runs/{rid}").json()
    store._runs.clear()  # simulate restart
    after = get(f"/api/runs/{rid}").json()
    assert after["summary"] == before["summary"] and after["step"] == 80
    assert after["events"] == before["events"]
    assert post(f"/api/runs/{rid}/advance").json()["finished"]


def test_import_result_and_continue():
    rid = run_with_demo_events(until=100)
    exported = get(f"/api/runs/{rid}/result").json()
    r = post("/api/runs/import", exported, cid="bob")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["import_check"]["summary_matches"] and body["import_check"]["scenario_hash_matches"]
    assert body["step"] == 100 and len(body["events"]) == 2
    assert post(f"/api/runs/{body['run_id']}/advance", cid="bob").json()["finished"]
    assert post("/api/runs/import", {"schema_version": "x"}).status_code == 400


def test_delete_run():
    rid = new_run()
    assert get(f"/api/runs/{rid}").status_code == 200
    assert client.delete(f"/api/runs/{rid}", headers=h()).status_code == 200
    assert get(f"/api/runs/{rid}").status_code == 404
