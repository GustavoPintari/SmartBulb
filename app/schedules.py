"""Recurring bulb actions, persisted as JSON and run by APScheduler.

A schedule fires at a fixed time or relative to sunrise/sunset on the chosen
weekdays; running the action itself is delegated to the `runner` callback.
"""
import json
import logging
import os
import threading
import uuid
from datetime import datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from .bulb import parse_color

log = logging.getLogger(__name__)

ACTIONS = ("on", "off", "color", "white", "brightness", "scene", "favorite", "wake", "sleep", "circadian")
TRIGGERS = ("time", "sunrise", "sunset")
DAY_NAMES = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
SUN_REFRESH_JOB = "_sun_refresh"


def validate(item, check=None):
    """Normalise and validate a schedule dict; raise ValueError on bad input.

    `check` (optional) verifies references: check(kind, value) for kind in
    "scene", "favorite", "target" and "sun".
    """
    trigger = item.get("trigger") or "time"
    if trigger not in TRIGGERS:
        raise ValueError(f"trigger must be one of {TRIGGERS}")
    offset = int(item.get("offset") or 0)
    if not -240 <= offset <= 240:
        raise ValueError("offset must be between -240 and 240 minutes")
    hour, minute = (int(p) for p in str(item.get("time") or "00:00").split(":"))
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
    elif action in ("scene", "favorite"):
        if not value:
            raise ValueError(f"{action} needs a value")
        if check:
            check(action, value)
    elif action == "brightness":
        value = int(value)
        if not 1 <= value <= 100:
            raise ValueError("brightness must be 1-100")
    elif action == "white" and value not in (None, ""):
        value = int(value)
        if not 0 <= value <= 100:
            raise ValueError("temperature must be 0-100")
    elif action in ("wake", "sleep"):
        value = int(value or 15)
        if not 1 <= value <= 120:
            raise ValueError("fade must last 1-120 minutes")
    else:
        value = None
    target = item.get("target") or None
    if check:
        check("target", target)
        if trigger != "time":
            check("sun", None)
    return {
        "id": item.get("id") or uuid.uuid4().hex[:8],
        "trigger": trigger,
        "time": f"{hour:02d}:{minute:02d}",
        "offset": offset if trigger != "time" else 0,
        "days": days,
        "action": action,
        "value": value,
        "target": target,
        "enabled": bool(item.get("enabled", True)),
    }


class ScheduleStore:
    def __init__(self, path, timezone, runner, sun, broker=None):
        self._path = path
        self._runner = runner
        self._sun = sun
        self._broker = broker
        self._lock = threading.Lock()
        self._scheduler = BackgroundScheduler(timezone=timezone)
        self._tz = timezone
        self._items = self._read()

    @property
    def scheduler(self):
        return self._scheduler

    def start(self):
        for item in self._items:
            self._register(item)
        # recompute sun-based runs every night (and pick up a changed location)
        self._scheduler.add_job(self.refresh_sun, CronTrigger(hour=0, minute=5, timezone=self._tz),
                                id=SUN_REFRESH_JOB, replace_existing=True)
        self._scheduler.start()

    def shutdown(self):
        self._scheduler.shutdown(wait=False)

    def refresh_sun(self):
        with self._lock:
            items = [i for i in self._items if i["trigger"] != "time"]
        for item in items:
            self._register(item)

    def list(self):
        with self._lock:
            result = [dict(i) for i in self._items]
        for item in result:
            job = self._scheduler.get_job(item["id"])
            item["next_run"] = job.next_run_time.isoformat() if job and job.next_run_time else None
        return sorted(result, key=lambda i: (i["next_run"] or "9999", i["time"]))

    def upsert(self, item, check=None):
        item = validate(item, check)
        with self._lock:
            self._items = [i for i in self._items if i["id"] != item["id"]] + [item]
            self._write()
        self._register(item)
        self._changed()
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
        self._changed()
        return True

    def _changed(self):
        if self._broker:
            self._broker.publish("schedules")

    def _register(self, item):
        if self._scheduler.get_job(item["id"]):
            self._scheduler.remove_job(item["id"])
        if not item["enabled"]:
            return
        if item["trigger"] == "time":
            hour, minute = item["time"].split(":")
            trigger = CronTrigger(
                hour=hour,
                minute=minute,
                day_of_week=",".join(DAY_NAMES[d] for d in item["days"]),
                timezone=self._tz,
            )
            self._scheduler.add_job(self._run, trigger, args=[item], id=item["id"], misfire_grace_time=120)
            return
        when = self._next_sun_run(item)
        if when:
            self._scheduler.add_job(self._run_sun, DateTrigger(when), args=[item["id"]], id=item["id"],
                                    misfire_grace_time=300)

    def _next_sun_run(self, item):
        now = datetime.now(self._tz)
        for ahead in range(8):
            day = now.date() + timedelta(days=ahead)
            if day.weekday() not in item["days"]:
                continue
            times = self._sun.times(day)
            if not times:
                continue
            when = times[0 if item["trigger"] == "sunrise" else 1] + timedelta(minutes=item["offset"])
            if when > now:
                return when
        return None

    def _run_sun(self, schedule_id):
        with self._lock:
            item = next((i for i in self._items if i["id"] == schedule_id), None)
        if item:
            self._run(item)
            self._register(item)
            self._changed()

    def _run(self, item):
        log.info("running schedule %s: %s %s", item["id"], item["action"], item["value"])
        try:
            self._runner(item)
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
