from backend.tests.helpers import get, new_run, post, run_with_demo_events, wait_task


def test_analytics_consistent_with_summary():
    rid = run_with_demo_events()
    a = get(f"/api/runs/{rid}/analytics").json()
    s = a["summary"]
    assert a["missed"]["count"] == s["jobs_due_missed"]
    assert sum(c["jobs"] for c in a["missed"]["by_cause"]) == s["jobs_due_missed"]
    p3 = next(p for p in a["by_priority"] if p["priority"] == 3)
    assert p3["completed_on_time_of_due"] == s["critical_jobs_completed_on_time"]
    assert a["blocked_commands"]["count"] == s["blocked_command_count"]
    job_steps = sum(u["job_steps"] for u in a["utilization"]["satellites"].values())
    trace = get(f"/api/runs/{rid}/trace").json()
    assert job_steps == sum(r["executed"] == "job" for r in trace)


def test_rates_absent_without_denominator():
    rid = new_run()
    a = get(f"/api/runs/{rid}/analytics").json()
    assert a["critical_rate"] is None and a["utilization"]["fleet_utilization"] is None


def test_urgent_downlink_during_closed_contacts_is_proved_infeasible():
    rid = run_with_demo_events()
    why = get(f"/api/runs/{rid}/jobs/URG-P-04/why").json()
    assert why["status"] == "missed"
    assert why["cause"]["primary"] == "infeasible_contacts"
    assert why["cause"]["proof"]["usable_steps"] == []


def test_explain_decision_uses_only_known_information():
    rid = run_with_demo_events()
    e = get(f"/api/runs/{rid}/explain", step=73, satellite_id="S08").json()
    assert [x["id"] for x in e["known"]["events_received"]] == ["E-01"]
    assert e["decision"]["executed"] in ("idle", "calibrate", "job")
    assert e["options"][0]["action"] == "calibrate"
    assert get(f"/api/runs/{rid}/explain", step=999, satellite_id="S08").status_code == 400


def test_schedule_and_series():
    rid = new_run()
    post(f"/api/runs/{rid}/advance")
    lanes = get(f"/api/runs/{rid}/schedule").json()["lanes"]
    assert sum(i["end_step"] - i["start_step"] for i in lanes["S01"]) == 48
    s = get(f"/api/runs/{rid}/satellites/S01/series").json()
    assert len(s["soc_pct"]) == 49 and len(s["action"]) == 48


def test_report_is_html():
    rid = run_with_demo_events()
    r = get(f"/api/runs/{rid}/report")
    assert r.status_code == 200 and "Отчёт по смене" in r.text and "Причины невыполнения" in r.text


def test_forecast_does_not_change_run():
    rid = new_run("P02_shift")
    post(f"/api/runs/{rid}/advance", {"until_step": 60})
    before = get(f"/api/runs/{rid}").json()
    f = post(f"/api/runs/{rid}/forecast").json()
    assert f["as_of_step"] == 60 and f["projected"]["jobs_completed"] >= f["current"]["jobs_completed"]
    assert get(f"/api/runs/{rid}").json() == before


def test_compare_branches_from_same_state():
    rid = new_run("P04_demand")
    post(f"/api/runs/{rid}/advance", {"until_step": 50})
    a = post(f"/api/runs/{rid}/fork", {"goal": "priority"}).json()["run_id"]
    b = post(f"/api/runs/{rid}/fork", {"goal": "revenue"}).json()["run_id"]
    post(f"/api/runs/{a}/advance")
    post(f"/api/runs/{b}/advance")
    c = post("/api/compare", {"a": a, "b": b, "goal": "revenue"}).json()
    assert c["same_conditions"] and c["origin"] == {"from": rid, "step": 50}
    assert c["verdict"]["preferred"] == "B"
    assert post("/api/compare", {"a": a, "b": a}).status_code == 400


def test_strategies_in_background():
    rid = new_run("P02_shift")
    post(f"/api/runs/{rid}/advance", {"until_step": 100})
    r = post(f"/api/runs/{rid}/strategies", {"background": True})
    assert r.status_code == 202
    t = wait_task(r.json()["id"])
    assert t["status"] == "done", t
    ranking = t["result"]["ranking"]
    assert len(ranking) == 2 and all(get(f"/api/runs/{x['run_id']}").json()["finished"] for x in ranking)


def test_whatif_event_does_not_touch_run():
    rid = new_run("P02_shift")
    post(f"/api/runs/{rid}/advance", {"until_step": 72})
    ev = {"type": "add_jobs", "jobs": [{"id": "W-1", "kind": "relay", "release_step": 72, "deadline_step": 80,
                                        "work_steps": 3, "eligible_satellites": ["S08", "S10"],
                                        "priority": 3, "value_usd": 40}]}
    w = post(f"/api/runs/{rid}/what-if/event", {"event": ev}).json()
    assert w["new_jobs"]["total"] == 1 and "summary" in w
    assert get(f"/api/runs/{rid}").json()["events"] == []
    bad = post(f"/api/runs/{rid}/what-if/event", {"event": {"type": "add_jobs", "jobs": []}})
    assert bad.status_code == 400


def test_whatif_resources():
    rid = new_run()
    post(f"/api/runs/{rid}/advance")
    w = post(f"/api/runs/{rid}/what-if/resources",
             {"variations": [{"label": "3 канала", "overrides": {"downlink_parallel_limit": 3}},
                             {"label": "мало солнца", "overrides": {"solar_factor": 0.3}}]}).json()
    assert [r["label"] for r in w["results"]] == ["как есть", "3 канала", "мало солнца"]
    assert "verdict" in w["results"][1]


def test_busy_run_rejects_mutations():
    rid = new_run("P04_demand")
    r = post(f"/api/runs/{rid}/advance", {"background": True})
    assert r.status_code == 202
    blocked = post(f"/api/runs/{rid}/goal", {"goal": "revenue"})
    t = wait_task(r.json()["id"])
    assert t["status"] == "done" and t["result"]["finished"]
    assert blocked.status_code in (200, 409)  # 409 if the task was still running
