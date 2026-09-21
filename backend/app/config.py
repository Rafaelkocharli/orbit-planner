"""Paths and settings. Everything is relative to the repo root or set via env."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("ORBIT_DATA_DIR", ROOT / "data"))
EXAMPLES_DIR = ROOT / "examples"
ALGORITHM_VERSION = "0.1.0"
MAX_RUNS = int(os.environ.get("ORBIT_MAX_RUNS", "200"))
