"""File storage: scenarios by content hash, run records (events + commands, no trace).

A run is restored by replaying its commands on the stored scenario, so the disk
record stays small and the restored state is recomputed, not trusted.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

from model.operations import digest

from .config import STORAGE_DIR

_SAFE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def safe_id(value: str, what: str = "id") -> str:
    if not isinstance(value, str) or not _SAFE.match(value):
        raise ValueError(f"Invalid {what}")
    return value


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, allow_nan=False)
    os.replace(tmp, path)


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


class Storage:
    def __init__(self, root: Path = STORAGE_DIR):
        self.root = Path(root)
        self.scenarios = self.root / "scenarios"
        self.custom = self.root / "custom"
        self.runs = self.root / "runs"

    # --- scenarios addressed by content hash (shared, immutable) ---
    def put_scenario(self, scenario: dict) -> str:
        h = digest(scenario)
        path = self.scenarios / f"{h}.json"
        if not path.exists():
            _write_json(path, scenario)
        return h

    def get_scenario(self, scenario_hash: str) -> dict:
        path = self.scenarios / f"{safe_id(scenario_hash, 'scenario hash')}.json"
        if not path.exists():
            raise KeyError(f"Scenario {scenario_hash} not stored")
        return _read_json(path)

    # --- operator-saved scenario variants (per client) ---
    def put_custom(self, client: str, custom_id: str, record: dict) -> None:
        _write_json(self.custom / safe_id(client, "client") / f"{safe_id(custom_id)}.json", record)

    def get_custom(self, client: str, custom_id: str) -> dict:
        path = self.custom / safe_id(client, "client") / f"{safe_id(custom_id)}.json"
        if not path.exists():
            raise KeyError(f"Scenario {custom_id} not found")
        return _read_json(path)

    def list_custom(self, client: str) -> list[dict]:
        folder = self.custom / safe_id(client, "client")
        return [_read_json(p) for p in sorted(folder.glob("*.json"))] if folder.exists() else []

    # --- runs ---
    def put_run(self, record: dict) -> None:
        folder = self.runs / safe_id(record["owner"], "client")
        _write_json(folder / f"{record['id']}.json", record)
        _write_json(folder / f"{record['id']}.meta.json",
                    {k: v for k, v in record.items() if k not in ("events", "commands")})

    def get_run(self, client: str, run_id: str) -> dict | None:
        path = self.runs / safe_id(client, "client") / f"{safe_id(run_id, 'run id')}.json"
        return _read_json(path) if path.exists() else None

    def list_runs(self, client: str) -> list[dict]:
        folder = self.runs / safe_id(client, "client")
        if not folder.exists():
            return []
        metas = [_read_json(p) for p in folder.glob("*.meta.json")]
        return sorted(metas, key=lambda m: m.get("created_at", 0), reverse=True)

    def delete_run(self, client: str, run_id: str) -> bool:
        folder = self.runs / safe_id(client, "client")
        found = False
        for suffix in (".json", ".meta.json"):
            p = folder / f"{safe_id(run_id, 'run id')}{suffix}"
            if p.exists():
                p.unlink()
                found = True
        return found
