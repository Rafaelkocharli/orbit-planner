"""Scenario catalog and operator overrides.

Changed conditions are a new experiment: the result is a new scenario that starts from step 0.
Built-in scenarios live in data/, operator variants are stored per client.
"""
from __future__ import annotations

import copy
import json
import time
import uuid

from model.resource_env import validate

from .config import DATA_DIR
from .storage import Storage

OVERRIDE_KEYS = {"initial_soc", "solar_factor", "job_priority", "failures", "downlink_parallel_limit"}


def _builtin_path(scenario_id: str):
    path = (DATA_DIR / f"{scenario_id}.json").resolve()
    if path.parent != DATA_DIR.resolve() or not path.exists():
        return None
    return path


def describe(s: dict) -> dict:
    return {"title": s["meta"]["title"], "steps": s["time"]["steps"],
            "satellites": len(s["satellites"]), "jobs": len(s["jobs"]),
            "downlink_parallel_limit": s["model"]["downlink_parallel_limit"]}


class ScenarioCatalog:
    def __init__(self, storage: Storage):
        self.storage = storage
        self._builtin_cache: dict[str, dict] = {}

    def builtin(self) -> list[dict]:
        out = []
        for p in sorted(DATA_DIR.glob("*.json")):
            out.append({"id": p.stem, "source": "builtin", **describe(self._load_builtin(p.stem))})
        return out

    def _load_builtin(self, scenario_id: str) -> dict:
        if scenario_id not in self._builtin_cache:
            path = _builtin_path(scenario_id)
            if path is None:
                raise ValueError(f"Unknown scenario: {scenario_id}")
            self._builtin_cache[scenario_id] = json.loads(path.read_text(encoding="utf-8"))
        return self._builtin_cache[scenario_id]

    def list(self, client: str) -> list[dict]:
        custom = [{k: v for k, v in r.items() if k != "scenario_hash"}
                  for r in self.storage.list_custom(client)]
        return self.builtin() + custom

    def get(self, client: str, scenario_id: str) -> dict:
        """Return a deep copy of a built-in or client-saved scenario."""
        if not isinstance(scenario_id, str) or not scenario_id:
            raise ValueError("scenario_id is required")
        if _builtin_path(scenario_id) is not None:
            return copy.deepcopy(self._load_builtin(scenario_id))
        try:
            record = self.storage.get_custom(client, scenario_id)
        except (KeyError, ValueError):
            raise ValueError(f"Unknown scenario: {scenario_id}")
        return self.storage.get_scenario(record["scenario_hash"])

    def save(self, client: str, scenario: dict, title: str | None = None,
             base_id: str | None = None, overrides: dict | None = None) -> dict:
        """Validate and store an uploaded or modified scenario under a new id."""
        validate(scenario)  # readable error before touching fields
        scenario = copy.deepcopy(scenario)
        custom_id = f"custom-{uuid.uuid4().hex[:8]}"
        if title:
            scenario["meta"]["title"] = str(title)[:200]
        scenario["meta"]["id"] = custom_id
        validate(scenario)
        record = {"id": custom_id, "source": "custom", "base_id": base_id,
                  "overrides": overrides or {}, "created_at": time.time(),
                  "scenario_hash": self.storage.put_scenario(scenario), **describe(scenario)}
        self.storage.put_custom(client, custom_id, record)
        return {k: v for k, v in record.items() if k != "scenario_hash"}


def apply_overrides(scenario: dict, ov: dict | None) -> dict:
    """ov keys:
    initial_soc {sid|"*": pct}, solar_factor float, job_priority {job_id: 1..3},
    failures [{satellite_id, start_step, end_step}], downlink_parallel_limit int.
    Returns a validated copy; the input is not modified.
    """
    if not ov:
        return scenario
    if not isinstance(ov, dict) or set(ov) - OVERRIDE_KEYS:
        raise ValueError(f"Unknown override keys: {sorted(set(ov) - OVERRIDE_KEYS)}")
    s = copy.deepcopy(scenario)
    sats = {v["id"]: v for v in s["satellites"]}
    for sid, pct in (ov.get("initial_soc") or {}).items():
        targets = list(sats) if sid == "*" else [sid]
        for t in targets:
            if t not in sats:
                raise ValueError(f"Unknown satellite {t}")
            sats[t]["initial_soc_pct"] = pct
    factor = ov.get("solar_factor")
    if factor is not None:
        if not isinstance(factor, (int, float)) or isinstance(factor, bool) or factor < 0:
            raise ValueError("solar_factor must be a non-negative number")
        for env in s["environment"].values():
            env["solar_w"] = [w * factor for w in env["solar_w"]]
    jobs = {j["id"]: j for j in s["jobs"]}
    for jid, p in (ov.get("job_priority") or {}).items():
        if jid not in jobs:
            raise ValueError(f"Unknown job {jid}")
        jobs[jid]["priority"] = p
    s["failures"].extend(copy.deepcopy(ov.get("failures") or []))
    if ov.get("downlink_parallel_limit") is not None:
        s["model"]["downlink_parallel_limit"] = ov["downlink_parallel_limit"]
    validate(s)
    return s
