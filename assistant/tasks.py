"""A tiny JSON-file task list with optional reminder times."""
from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime
from pathlib import Path


class TaskStore:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()

    def _read(self) -> list[dict]:
        try:
            return json.loads(self.path.read_text())
        except (OSError, ValueError):
            return []

    def _write(self, tasks: list[dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(tasks, indent=2))
        tmp.replace(self.path)

    def add(self, title: str, remind_at: str | None = None) -> dict:
        """remind_at: local time 'YYYY-MM-DDTHH:MM' or None."""
        if remind_at:
            datetime.fromisoformat(remind_at)  # validate; raises ValueError
        task = {"id": uuid.uuid4().hex[:6], "title": title.strip(), "remind_at": remind_at or None,
                "done": False, "reminded": False}
        with self._lock:
            tasks = self._read()
            tasks.append(task)
            self._write(tasks)
        return task

    def open_tasks(self) -> list[dict]:
        with self._lock:
            return [t for t in self._read() if not t["done"]]

    def complete(self, task_id_or_title: str) -> dict | None:
        key = task_id_or_title.strip().lower()
        with self._lock:
            tasks = self._read()
            match = next((t for t in tasks if not t["done"] and t["id"] == key), None) or next(
                (t for t in tasks if not t["done"] and key in t["title"].lower()), None)
            if match:
                match["done"] = True
                self._write(tasks)
            return match

    def due_reminders(self, now_local: datetime) -> list[dict]:
        """Open tasks whose reminder time has passed and that were not reminded yet."""
        naive_now = now_local.replace(tzinfo=None)
        with self._lock:
            return [t for t in self._read()
                    if not t["done"] and not t["reminded"] and t.get("remind_at")
                    and datetime.fromisoformat(t["remind_at"]) <= naive_now]

    def mark_reminded(self, task_id: str) -> None:
        with self._lock:
            tasks = self._read()
            for t in tasks:
                if t["id"] == task_id:
                    t["reminded"] = True
            self._write(tasks)
