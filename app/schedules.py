"""Recurring bulb actions, persisted as JSON and run by APScheduler."""
import json
import logging
import os
import threading
import uuid

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from .bulb import SCENES, Bulb, parse_color

log = logging.getLogger(__name__)

ACTIONS = ("on", "off", "color", "white", "brightness", "scene")
DAY_NAMES = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def validate(item):
    """Normalise and validate a schedule dict; raise ValueError on bad input."""
    hour, minute = (int(p) for p in str(item["time"]).split(":"))
    if not (0 <= hour < 24 and 0 <= minute < 60):
        raise ValueError("time must be HH:MM")
    days = sorted({int(d) for d in item.get("days") or range(7)})
    if any(d not in range(7) for d in days):
        raise ValueError("days must be 0 (mon) to 6 (sun)")
    action = item["action"]
    if action not in ACTIONS:
        raise ValueError(f"action must be one of {ACTIONS}")
    value = item.get("value")
    if action == "color":
        parse_color(str(value))
    elif action == "scene":
        if value not in SCENES:
            raise ValueError(f"unknown scene: {value}")
    elif action == "brightness":
        value = int(value)
        if not 1 <= value <= 100:
            raise ValueError("brightness must be 1-100")
    elif action == "white" and value not in (None, ""):
        value = int(value)
        if not 0 <= value <= 100:
            raise ValueError("temperature must be 0-100")
    else:
        value = None
    return {
        "id": item.get("id") or uuid.uuid4().hex[:8],
        "time": f"{hour:02d}:{minute:02d}",
        "days": days,
        "action": action,
        "value": value,
        "enabled": bool(item.get("enabled", True)),
    }


class ScheduleStore:
    def __init__(self, bulb: Bulb, path: str, timezone: str | None = None):
        self._bulb = bulb
        self._path = path
        self._lock = threading.Lock()
        self._scheduler = BackgroundScheduler(timezone=timezone)
        self._items = self._read()

    def start(self):
        for item in self._items:
            self._register(item)
        self._scheduler.start()

    def shutdown(self):
        self._scheduler.shutdown(wait=False)

    def list(self):
        with self._lock:
            result = [dict(i) for i in self._items]
        for item in result:
            job = self._scheduler.get_job(item["id"])
            item["next_run"] = job.next_run_time.isoformat() if job and job.next_run_time else None
        return sorted(result, key=lambda i: i["time"])

    def upsert(self, item):
        item = validate(item)
        with self._lock:
            self._items = [i for i in self._items if i["id"] != item["id"]] + [item]
            self._write()
        self._register(item)
        return item

    def delete(self, schedule_id):
        with self._lock:
            before = len(self._items)
            self._items = [i for i in self._items if i["id"] != schedule_id]
            if len(self._items) == before:
                return False
            self._write()
        if self._scheduler.get_job(schedule_id):
            self._scheduler.remove_job(schedule_id)
        return True

    def _register(self, item):
        if self._scheduler.get_job(item["id"]):
            self._scheduler.remove_job(item["id"])
        if not item["enabled"]:
            return
        hour, minute = item["time"].split(":")
        trigger = CronTrigger(
            hour=hour,
            minute=minute,
            day_of_week=",".join(DAY_NAMES[d] for d in item["days"]),
            timezone=self._scheduler.timezone,
        )
        self._scheduler.add_job(
            self._run, trigger, args=[item], id=item["id"], misfire_grace_time=120
        )

    def _run(self, item):
        action, value = item["action"], item["value"]
        log.info("running schedule %s: %s %s", item["id"], action, value)
        try:
            if action == "on":
                self._bulb.turn_on()
            elif action == "off":
                self._bulb.turn_off()
            elif action == "color":
                self._bulb.set_color(value)
            elif action == "white":
                self._bulb.set_white(value)
            elif action == "brightness":
                self._bulb.set_brightness(value)
            elif action == "scene":
                self._bulb.set_scene(value)
        except Exception:
            log.exception("schedule %s failed", item["id"])

    def _read(self):
        try:
            with open(self._path) as f:
                return [validate(i) for i in json.load(f)]
        except FileNotFoundError:
            return []

    def _write(self):
        os.makedirs(os.path.dirname(self._path) or ".", exist_ok=True)
        tmp = self._path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self._items, f, indent=2)
        os.replace(tmp, self._path)
