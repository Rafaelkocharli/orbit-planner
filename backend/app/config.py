"""Paths and settings. Everything is relative to the repo root or set via env."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("ORBIT_DATA_DIR", ROOT / "data"))
EXAMPLES_DIR = ROOT / "examples"
STORAGE_DIR = Path(os.environ.get("ORBIT_STORAGE_DIR", ROOT / "storage"))
ALGORITHM_VERSION = "0.2.0"
# Runs kept in memory; older ones are evicted and restored from disk on demand.
MAX_RUNS_IN_MEMORY = int(os.environ.get("ORBIT_MAX_RUNS", "64"))
TASK_WORKERS = int(os.environ.get("ORBIT_TASK_WORKERS", "2"))
DEFAULT_CLIENT = "public"
