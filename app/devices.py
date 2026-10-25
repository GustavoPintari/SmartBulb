"""Bulbs, groups, a status cache refreshed in the background, undo history and usage stats."""
import logging
import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

from .bulb import Bulb, BulbError, state_from_dps

log = logging.getLogger(__name__)

UNDO_DEPTH = 10
STATS_DAYS = 90


class Device:
    def __init__(self, config):
        self.id = config.device_id
        self.name = config.name
        self.watts = config.watts
        self.bulb = Bulb(config)
        self.dps = None        # last raw status
        self.online = None     # None until the first read
        self.updated = 0.0     # monotonic time of the last read attempt
        self.history = []      # dps snapshots for undo
        self.accounted = None  # monotonic time usage was last counted up to


class Registry:
    def __init__(self, configs, groups, stats, broker, poll_seconds=5.0):
        self.devices = {c.device_id: Device(c) for c in configs}
        if not self.devices:
            raise SystemExit("no bulbs configured")
        self._groups = groups    # JsonStore: [{id, name, members}]
        self._stats = stats      # JsonStore: {date: {device id: [seconds on, Wh]}}
        self._broker = broker
        self.poll_seconds = poll_seconds
        self.listeners = []      # fn(device, state) called on every change
        self.scene_lookup = lambda data: None
        self._pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="bulb")
        self._stop = threading.Event()
        self._last_flush = time.monotonic()

    # --- targets: a bulb id, a group id or "all"

    @property
    def default_target(self):
        return next(iter(self.devices))

    def groups(self):
        with self._groups.lock:
            return [dict(g) for g in self._groups.data]

    def targets(self):
        result = [{"id": d.id, "name": d.name, "kind": "bulb", "members": [d.id]}
                  for d in self.devices.values()]
        result += [{**g, "kind": "group"} for g in self.groups()]
        if len(self.devices) > 1:
            result.append({"id": "all", "name": "Todas", "kind": "group", "members": list(self.devices)})
        return result

    def resolve(self, target):
        target = target or self.default_target
        if target in self.devices:
            return [self.devices[target]]
        if target == "all":
            return list(self.devices.values())
        group = next((g for g in self.groups() if g["id"] == target), None)
        members = [self.devices[m] for m in (group or {}).get("members", []) if m in self.devices]
        if not members:
            raise LookupError(f"unknown target: {target}")
        return members

    def target_name(self, target):
        target = target or self.default_target
        return next((t["name"] for t in self.targets() if t["id"] == target), target)

    def save_group(self, name, members, group_id=None):
        name = (name or "").strip()[:40]
        if not name:
            raise ValueError("dê um nome ao grupo")
        members = [m for m in dict.fromkeys(members) if m in self.devices]
        if not members:
            raise ValueError("escolha pelo menos uma lâmpada")
        with self._groups.lock:
            groups = self._groups.data
            if group_id:
                group = next((g for g in groups if g["id"] == group_id), None)
                if not group:
                    raise LookupError("grupo não encontrado")
                group.update(name=name, members=members)
            else:
                group = {"id": "g" + secrets.token_hex(3), "name": name, "members": members}
                groups.append(group)
            self._groups.save()
        self._broker.publish("targets")
        return group

    def delete_group(self, group_id):
        with self._groups.lock:
            before = len(self._groups.data)
            self._groups.data[:] = [g for g in self._groups.data if g["id"] != group_id]
            if len(self._groups.data) == before:
                raise LookupError("grupo não encontrado")
            self._groups.save()
        self._broker.publish("targets")

    # --- state

    def state(self, device):
        if device.dps is None:
            return None
        state = state_from_dps(device.dps)
        state["scene"] = self.scene_lookup(state.pop("scene_data"))
        return state

    def refresh(self, device):
        try:
            dps = device.bulb.dps()
        except BulbError:
            self._mark_offline(device)
            raise
        self._update(device, dps)
        return dps

    def _update(self, device, dps):
        now = time.monotonic()
        self._account(device, now)
        changed = dps != device.dps or device.online is not True
        device.dps, device.online, device.updated = dps, True, now
        if changed:
            self._notify(device)

    def _mark_offline(self, device):
        device.updated = time.monotonic()
        device.accounted = None
        if device.online is not False:
            device.online = False
            self._notify(device)

    def _notify(self, device):
        state = self.state(device)
        self._broker.publish("status", {"device": device.id, "online": device.online, "state": state})
        for listener in self.listeners:
            try:
                listener(device, state)
            except Exception:
                log.exception("status listener failed")

    def status(self, target, max_age=2.0):
        """State of the target's first reachable bulb, plus every member's on/online."""
        devices = self.resolve(target)
        for device in devices:
            if time.monotonic() - device.updated > max_age:
                try:
                    self.refresh(device)
                except BulbError:
                    pass
        primary = next((d for d in devices if d.online and d.dps is not None), None)
        if not primary:
            raise BulbError("lâmpada sem resposta")
        state = self.state(primary)
        state["online"] = True
        state["members"] = [{"id": d.id, "name": d.name, "online": bool(d.online),
                             "on": bool(d.dps and d.dps.get("20"))} for d in devices]
        return state

    # --- commands

    def apply(self, devices, fn, remember=False, refresh=True):
        """Run fn(device) on every device in parallel; fail only if all of them fail."""
        def one(device):
            try:
                if remember and device.dps is not None:
                    device.history.append(dict(device.dps))
                    del device.history[:-UNDO_DEPTH]
                fn(device)
                if refresh:
                    self.refresh(device)
                return None
            except (BulbError, OSError) as exc:
                self._mark_offline(device)
                return exc

        errors = [e for e in self._pool.map(one, devices) if e]
        if errors and len(errors) == len(devices):
            raise errors[0] if isinstance(errors[0], BulbError) else BulbError(str(errors[0]))
        for exc in errors:
            log.warning("command failed on one bulb: %s", exc)

    def run(self, target, fn, remember=False):
        self.apply(self.resolve(target), fn, remember)

    def undo(self, devices):
        devices = [d for d in devices if d.history]
        if not devices:
            raise ValueError("nada para desfazer")
        snapshots = {d.id: d.history.pop() for d in devices}
        self.apply(devices, lambda d: d.bulb.restore(snapshots[d.id]))

    # --- background polling and usage accounting

    def start(self):
        threading.Thread(target=self._loop, name="poller", daemon=True).start()

    def stop(self):
        self._stop.set()
        self._flush_stats(force=True)

    def _loop(self):
        while not self._stop.wait(1.0):
            for device in self.devices.values():
                if time.monotonic() - device.updated >= self.poll_seconds:
                    try:
                        self.refresh(device)
                    except BulbError:
                        pass
                    except Exception:
                        log.exception("polling %s failed", device.id)
            self._flush_stats()

    def _account(self, device, now):
        last, device.accounted = device.accounted, now
        if last is None or not device.dps or not device.dps.get("20"):
            return
        seconds = min(now - last, self.poll_seconds * 3)
        brightness = state_from_dps(device.dps)["brightness"]
        watt_hours = seconds * device.watts * (0.15 + 0.85 * brightness / 100) / 3600
        with self._stats.lock:
            bucket = self._stats.data.setdefault(date.today().isoformat(), {}).setdefault(device.id, [0, 0])
            bucket[0] += seconds
            bucket[1] += watt_hours

    def _flush_stats(self, force=False):
        if not force and time.monotonic() - self._last_flush < 60:
            return
        self._last_flush = time.monotonic()
        oldest = (date.today() - timedelta(days=STATS_DAYS)).isoformat()
        with self._stats.lock:
            for day in [d for d in self._stats.data if d < oldest]:
                del self._stats.data[day]
            self._stats.save()

    def usage(self, devices, days=7):
        """Hours on and Wh per day for the given devices, oldest first."""
        ids = {d.id for d in devices}
        result = []
        with self._stats.lock:
            for offset in range(days - 1, -1, -1):
                day = (date.today() - timedelta(days=offset)).isoformat()
                buckets = [v for k, v in self._stats.data.get(day, {}).items() if k in ids]
                result.append({
                    "date": day,
                    "hours": round(sum(b[0] for b in buckets) / 3600, 2),
                    "wh": round(sum(b[1] for b in buckets), 1),
                })
        return result
