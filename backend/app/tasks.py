"""Background tasks for long operations (whole-shift runs, strategy sweeps, what-ifs).

A task reports progress in [0, 1] and ends with a result or a readable error.
"""
from __future__ import annotations

import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from .config import TASK_WORKERS

Progress = Callable[[float, str], None]


class TaskManager:
    def __init__(self, workers: int = TASK_WORKERS, keep: int = 200):
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="orbit-task")
        self._tasks: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._keep = keep

    def submit(self, owner: str, kind: str, fn: Callable[[Progress], dict]) -> dict:
        tid = uuid.uuid4().hex[:12]
        task = {"id": tid, "owner": owner, "kind": kind, "status": "queued", "progress": 0.0,
                "message": "", "result": None, "error": None, "created_at": time.time()}
        with self._lock:
            if len(self._tasks) >= self._keep:
                done = [k for k, t in self._tasks.items() if t["status"] in ("done", "failed")]
                for k in done[: len(done) // 2 or 1]:
                    self._tasks.pop(k, None)
            self._tasks[tid] = task

        def progress(value: float, message: str = "") -> None:
            task["progress"] = round(min(1.0, max(0.0, value)), 4)
            task["message"] = message

        def work():
            task["status"] = "running"
            try:
                task["result"] = fn(progress)
                task["progress"] = 1.0
                task["status"] = "done"
            except (ValueError, KeyError, TypeError) as e:
                task["status"], task["error"] = "failed", str(e)
            except Exception as e:  # keep the service alive, report the failure
                task["status"], task["error"] = "failed", f"Internal error: {e}"
                traceback.print_exc()

        self._pool.submit(work)
        return self.public(task)

    def get(self, owner: str, task_id: str) -> dict:
        task = self._tasks.get(task_id)
        if task is None or task["owner"] != owner:
            raise KeyError(f"Task {task_id} not found")
        return self.public(task)

    @staticmethod
    def public(task: dict) -> dict:
        return {k: v for k, v in task.items() if k != "owner"}
