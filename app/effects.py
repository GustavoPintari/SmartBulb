"""Light routines that run over time: fades, pomodoro, alert flashes,
the circadian rhythm and presence simulation."""
import logging
import random
import threading
import time
from datetime import datetime

from .bulb import BulbError

log = logging.getLogger(__name__)

WAKE_FROM, WAKE_TO = (1, 0), (100, 70)   # (brightness, temperature) percent
POMODORO_WORK = (100, 85)                # cool, bright white
POMODORO_BREAK = ("#5eead4", 60)         # soft teal
PRESENCE_END = (23, 30)


class Effect:
    def __init__(self, kind, devices, **info):
        self.kind = kind
        self.devices = devices
        self.cancelled = threading.Event()
        self.restore = False  # set by stop(): put the light back as it was before
        self.info = {"kind": kind, **info}


class Effects:
    def __init__(self, registry, broker, settings, sun, activity, timezone):
        self._registry = registry
        self._broker = broker
        self._settings = settings  # JsonStore shared with the rest of the app
        self._sun = sun
        self._activity = activity
        self._tz = timezone
        self._active = {}  # device id -> Effect
        self._lock = threading.Lock()
        self._presence_lit = False
        settings.data.setdefault("circadian", [])
        settings.data.setdefault("presence", {"enabled": False, "target": None})

    # --- running effects

    def active(self, device_id):
        effect = self._active.get(device_id)
        return dict(effect.info) if effect else None

    def is_running(self, device_id):
        return device_id in self._active

    def _start(self, kind, devices, body, **info):
        effect = Effect(kind, devices, **info)
        with self._lock:
            for device in devices:
                previous = self._active.get(device.id)
                if previous:
                    previous.cancelled.set()
                self._active[device.id] = effect
        threading.Thread(target=self._run, args=(effect, body), name=kind, daemon=True).start()
        self._publish(effect)
        return effect.info

    def _run(self, effect, body):
        try:
            body(effect)
        except Exception:
            log.exception("effect %s failed", effect.kind)
        finally:
            with self._lock:
                for device in effect.devices:
                    if self._active.get(device.id) is effect:
                        del self._active[device.id]
            self._publish(effect)

    def stop(self, devices, restore=False):
        """Cancel effects on these devices; `restore` asks the effect to undo its changes."""
        with self._lock:
            effects = {id(e): e for e in (self._active.pop(d.id, None) for d in devices) if e}
        for effect in effects.values():
            effect.restore = restore
            effect.cancelled.set()
            self._publish(effect)

    def _publish(self, effect):
        for device in effect.devices:
            self._broker.publish("effect", {"device": device.id, "effect": self.active(device.id)})

    def _apply(self, devices, fn, refresh=True):
        """Best effort inside effects: an unreachable bulb shouldn't end the routine."""
        try:
            self._registry.apply(devices, fn, refresh=refresh)
        except BulbError as exc:
            log.warning("effect step failed: %s", exc)

    def _snapshot(self, devices):
        return {d.id: dict(d.dps) for d in devices if d.dps}

    def _restore(self, devices, snapshot):
        self._apply([d for d in devices if d.id in snapshot], lambda d: d.bulb.restore(snapshot[d.id]))

    @staticmethod
    def _deadline(minutes):
        return int((time.time() + minutes * 60) * 1000)

    # --- fades: "wake" (sunrise) and "sleep" (dims, then turns off)

    def fade(self, devices, direction, minutes):
        if direction not in ("wake", "sleep"):
            raise ValueError("direção deve ser wake ou sleep")
        if not 1 <= minutes <= 120:
            raise ValueError("duração deve ser de 1 a 120 minutos")
        steps = max(2, min(120, int(minutes * 60 // 10)))
        interval = minutes * 60 / steps
        snapshot = self._snapshot(devices)

        def start_point(device):
            if direction == "wake":
                return WAKE_FROM
            state = self._registry.state(device) or {}
            if not state.get("on"):
                return None
            temperature = state["temperature"] if state.get("mode") == "white" else 30
            return state.get("brightness", 100), temperature

        def body(effect):
            starts = {d.id: start_point(d) for d in devices}
            active = [d for d in devices if starts[d.id]]
            end = WAKE_TO if direction == "wake" else (1, 0)
            for step in range(0 if direction == "wake" else 1, steps + 1):
                if step and effect.cancelled.wait(interval):
                    return
                fraction = step / steps
                effect.info["progress"] = round(fraction, 3)

                def set_step(device):
                    (b0, t0), (b1, t1) = starts[device.id], end
                    device.bulb.set_white(round(t0 + (t1 - t0) * fraction),
                                          max(1, round(b0 + (b1 - b0) * fraction)))
                self._apply(active, set_step)
            if direction == "sleep":
                # off, but with the previous look kept for the next time it's turned on
                self._apply(active, lambda d: d.bulb.restore(snapshot[d.id], power=False))

        label = "Despertar" if direction == "wake" else "Adormecer"
        return self._start(direction, devices, body, label=label, ends_at=self._deadline(minutes))

    # --- pomodoro: focus light, then a coloured break, repeated

    def pomodoro(self, devices, work=25, rest=5, cycles=4):
        if not (1 <= work <= 120 and 1 <= rest <= 60 and 1 <= cycles <= 12):
            raise ValueError("pomodoro: foco 1-120 min, pausa 1-60 min, 1-12 ciclos")
        snapshot = self._snapshot(devices)

        def phase(effect, name, cycle, minutes):
            effect.info.update(phase=name, cycle=cycle, ends_at=self._deadline(minutes))
            self._publish(effect)
            return effect.cancelled.wait(minutes * 60)

        def body(effect):
            for cycle in range(1, cycles + 1):
                brightness, temperature = POMODORO_WORK
                self._apply(devices, lambda d: d.bulb.set_white(temperature, brightness))
                if phase(effect, "focus", cycle, work):
                    break
                self._blink(devices, "#ffffff", 2)
                if cycle == cycles:
                    break
                colour, brightness = POMODORO_BREAK
                self._apply(devices, lambda d: d.bulb.set_color(colour, brightness))
                if phase(effect, "break", cycle, rest):
                    break
                self._blink(devices, "#ffb000", 2)
            if not effect.cancelled.is_set() or effect.restore:
                self._restore(devices, snapshot)

        return self._start("pomodoro", devices, body, label="Pomodoro", phase="focus", cycle=1,
                           cycles=cycles, work=work, rest=rest, ends_at=self._deadline(work))

    def _blink(self, devices, colour, times):
        for _ in range(times):
            self._apply(devices, lambda d: d.bulb.set_color(colour, 100), refresh=False)
            time.sleep(0.5)
            self._apply(devices, lambda d: d.bulb.turn_off(), refresh=False)
            time.sleep(0.4)

    # --- flash: an alert (doorbell, failed build...) that returns to the previous look

    def flash(self, devices, colour, times=3):
        if not 1 <= times <= 10:
            raise ValueError("repetições: 1 a 10")
        snapshot = self._snapshot(devices)

        def body(effect):
            self._blink(devices, colour, times)
            self._restore(devices, snapshot)

        return self._start("flash", devices, body, label="Alerta")

    # --- circadian rhythm: white tone and brightness follow the time of day

    def circadian_enabled(self, device_id):
        return device_id in self._settings.data["circadian"]

    def set_circadian(self, devices, enabled):
        ids = {d.id for d in devices}
        with self._settings.lock:
            current = set(self._settings.data["circadian"])
            current = current | ids if enabled else current - ids
            self._settings.data["circadian"] = sorted(current)
            self._settings.save()
        self._broker.publish("settings")
        if enabled:
            self.circadian_tick(devices)

    def disable_circadian(self, devices):
        if any(self.circadian_enabled(d.id) for d in devices):
            self.set_circadian(devices, False)

    def circadian_point(self, now=None):
        """(temperature, brightness) in percent for this moment of the day."""
        now = now or datetime.now(self._tz)
        times = self._sun.times(now.date())
        sunrise, sunset = ((t.hour * 60 + t.minute for t in times) if times else (6 * 60 + 30, 18 * 60))
        points = sorted([
            (0, 0, 15),
            (sunrise - 30, 10, 30),
            (sunrise + 90, 70, 100),
            ((sunrise + sunset) // 2, 100, 100),
            (sunset - 60, 60, 90),
            (sunset + 60, 20, 60),
            (22 * 60 + 30, 0, 30),
            (24 * 60, 0, 15),
        ])
        minute = now.hour * 60 + now.minute
        for (m0, t0, b0), (m1, t1, b1) in zip(points, points[1:]):
            if m0 <= minute <= m1:
                f = (minute - m0) / (m1 - m0) if m1 > m0 else 0
                return round(t0 + (t1 - t0) * f), round(b0 + (b1 - b0) * f)
        return points[-1][1], points[-1][2]

    def circadian_tick(self, devices=None):
        """Bring every lit bulb in circadian mode to the current point (runs every few minutes)."""
        temperature, brightness = self.circadian_point()
        due = []
        for device in devices or self._registry.devices.values():
            state = self._registry.state(device)
            if not (self.circadian_enabled(device.id) and state and state["on"]) or self.is_running(device.id):
                continue
            if (state["mode"] != "white" or abs(state["temperature"] - temperature) > 1
                    or abs(state["brightness"] - brightness) > 1):
                due.append(device)
        if due:
            self._apply(due, lambda d: d.bulb.set_white(temperature, brightness))

    # --- presence simulation: lights on and off at random in the evening

    def presence(self):
        return dict(self._settings.data["presence"])

    def set_presence(self, enabled, target):
        if target:
            self._registry.resolve(target)
        with self._settings.lock:
            self._settings.data["presence"] = {"enabled": bool(enabled), "target": target or None}
            self._settings.save()
        self._broker.publish("settings")

    def presence_tick(self):
        config = self.presence()
        if not config["enabled"]:
            return
        devices = self._registry.resolve(config["target"])
        now = datetime.now(self._tz)
        times = self._sun.times(now.date())
        start = times[1] if times else now.replace(hour=18, minute=30)
        end = now.replace(hour=PRESENCE_END[0], minute=PRESENCE_END[1], second=0)
        if start <= now < end:
            if random.random() < 0.35:
                self._presence_lit = not self._presence_lit
                lit = self._presence_lit
                self._apply(devices, lambda d: d.bulb.set_white(15, 70) if lit else d.bulb.turn_off())
                self._activity.add("simulação de presença", "on" if lit else "off",
                                   self._registry.target_name(config["target"]))
        elif self._presence_lit:
            self._presence_lit = False
            self._apply(devices, lambda d: d.bulb.turn_off())
            self._activity.add("simulação de presença", "off", self._registry.target_name(config["target"]))
