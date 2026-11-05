"""Background loop that phones you at check-in times and task reminder times."""
from __future__ import annotations

import logging
import threading
from datetime import datetime
from zoneinfo import ZoneInfo

from .config import Settings
from .tasks import TaskStore

log = logging.getLogger(__name__)


def due_checkins(now: datetime, checkins: dict[str, str], days: set[int],
                 fired: set[str]) -> list[str]:
    """Check-ins whose time has arrived today and that haven't fired yet.

    A check-in fires once, during its own minute or up to 10 minutes late (so a
    restart doesn't skip it, but a server that was down all morning doesn't
    phone you with a stale briefing)."""
    if now.weekday() not in days:
        return []
    due = []
    minutes_now = now.hour * 60 + now.minute
    for name, hhmm in checkins.items():
        key = f"{now:%Y-%m-%d}:{name}"
        hh, mm = map(int, hhmm.split(":"))
        if key not in fired and 0 <= minutes_now - (hh * 60 + mm) <= 10:
            due.append(name)
    return due


class Scheduler(threading.Thread):
    def __init__(self, settings: Settings, tasks: TaskStore, place_call, interval: float = 20.0):
        super().__init__(daemon=True, name="scheduler")
        self.s, self.tasks, self.place_call, self.interval = settings, tasks, place_call, interval
        self.fired: set[str] = set()
        self.stop_event = threading.Event()

    def tick(self, now: datetime | None = None) -> list[str]:
        now = now or datetime.now(ZoneInfo(self.s.timezone))
        placed = []
        for name in due_checkins(now, self.s.checkins, self.s.checkin_days, self.fired):
            self.fired.add(f"{now:%Y-%m-%d}:{name}")
            try:
                self.place_call(name)
                placed.append(name)
            except Exception:
                log.exception("check-in call %s failed", name)
        for task in self.tasks.due_reminders(now):
            self.tasks.mark_reminded(task["id"])  # mark first: never call twice for one reminder
            try:
                self.place_call("reminder", f"This is a REMINDER call for the task: "
                                            f"{task['title']!r} (id {task['id']}). Remind them, ask "
                                            f"if it's done, and keep it short.")
                placed.append(f"reminder:{task['id']}")
            except Exception:
                log.exception("reminder call for %s failed", task["id"])
        return placed

    def run(self) -> None:
        log.info("scheduler running: %s", self.s.checkins)
        while not self.stop_event.wait(self.interval):
            try:
                self.tick()
            except Exception:
                log.exception("scheduler tick failed")
