"""Scenario loading and operator overrides. Overrides create a new scenario (new run from step 0)."""
from __future__ import annotations

import copy
import json

from model.resource_env import validate

from .config import DATA_DIR


def list_scenarios() -> list[dict]:
    out = []
    for p in sorted(DATA_DIR.glob("*.json")):
        s = json.loads(p.read_text(encoding="utf-8"))
        out.append({"id": p.stem, "title": s["meta"]["title"], "steps": s["time"]["steps"],
                    "satellites": len(s["satellites"]), "jobs": len(s["jobs"])})
    return out


def load_scenario(scenario_id: str) -> dict:
    path = (DATA_DIR / f"{scenario_id}.json").resolve()
    if path.parent != DATA_DIR.resolve() or not path.exists():
        raise ValueError(f"Unknown scenario: {scenario_id}")
    return json.loads(path.read_text(encoding="utf-8"))


def apply_overrides(scenario: dict, ov: dict | None) -> dict:
    """ov: {initial_soc: {sid: pct}, solar_factor: float, job_priority: {job_id: p},
    failures: [{satellite_id, start_step, end_step}]}"""
    if not ov:
        return scenario
    s = copy.deepcopy(scenario)
    sats = {v["id"]: v for v in s["satellites"]}
    for sid, pct in (ov.get("initial_soc") or {}).items():
        if sid not in sats:
            raise ValueError(f"Unknown satellite {sid}")
        sats[sid]["initial_soc_pct"] = pct
    factor = ov.get("solar_factor")
    if factor is not None:
        for env in s["environment"].values():
            env["solar_w"] = [w * factor for w in env["solar_w"]]
    jobs = {j["id"]: j for j in s["jobs"]}
    for jid, p in (ov.get("job_priority") or {}).items():
        if jid not in jobs:
            raise ValueError(f"Unknown job {jid}")
        jobs[jid]["priority"] = p
    s["failures"].extend(ov.get("failures") or [])
    s["meta"]["id"] = s["meta"]["id"] + "-custom"
    validate(s)
    return s
