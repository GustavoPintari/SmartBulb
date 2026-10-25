"""Local control of a Tuya bulb (no cloud).

DPS map for this bulb: 20 power, 21 mode (white/colour/scene),
22 white brightness (10-1000), 23 colour temperature (0-1000),
24 colour HSV hex (hhhhssssvvvv), 25 scene data, 26 countdown in seconds.
"""
import colorsys
import json
import os
import threading
import time
from dataclasses import dataclass

import tinytuya

_COLORS = [
    # (english, portuguese, rgb)
    ("red", "vermelho", (255, 0, 0)),
    ("green", "verde", (0, 255, 0)),
    ("blue", "azul", (0, 0, 255)),
    ("yellow", "amarelo", (255, 200, 0)),
    ("orange", "laranja", (255, 100, 0)),
    ("purple", "roxo", (140, 0, 255)),
    ("pink", "rosa", (255, 0, 120)),
    ("cyan", "ciano", (0, 255, 255)),
    ("white", "branco", (255, 255, 255)),
]
COLOR_NAMES = {name: rgb for en, pt, rgb in _COLORS for name in (en, pt)}
DEFAULT_WATTS = 9.0

# --- scene data (DPS 25, scene_data v2)
# A scene is a number (2 hex digits) followed by up to 8 units of
# interval(2) duration(2) mode(2) H(4) S(4) V(4) bright(4) temp(4).
# interval/duration go from 0 to 100; larger values hold each step longer.
STATIC, JUMP, FADE = 0, 1, 2
SLOW, MEDIUM, FAST = 0x5a, 0x46, 0x14


def _unit(mode, speed, h=0, s=0, v=0, bright=0, temp=0):
    return f"{speed:02x}{speed:02x}{mode:02x}{h:04x}{s:04x}{v:04x}{bright:04x}{temp:04x}"


def white_unit(bright, temp, mode=STATIC, speed=MEDIUM):
    """bright and temp in percent (temp 0 = warm, 100 = cool)."""
    return _unit(mode, speed, bright=max(10, bright * 10), temp=temp * 10)


def colour_unit(hue, sat, val, mode=STATIC, speed=MEDIUM):
    """hue in degrees, sat and val in percent."""
    return _unit(mode, speed, h=hue % 360, s=sat * 10, v=max(10, val * 10))


def encode_scene(number, units):
    return f"{number:02x}" + "".join(units)


def decode_scene(data):
    units = []
    for i in range(2, len(data) - 25, 26):
        u = data[i:i + 26]
        units.append({
            "mode": int(u[4:6], 16), "h": int(u[6:10], 16), "s": int(u[10:14], 16),
            "v": int(u[14:18], 16), "bright": int(u[18:22], 16), "temp": int(u[22:26], 16),
        })
    return units


def temperature_rgb(temp):
    """Approximate RGB for a white temperature in percent (0 = warm)."""
    warm, cool = (255, 179, 92), (207, 230, 255)
    return tuple(round(a + (b - a) * temp / 100) for a, b in zip(warm, cool))


def unit_preview(unit):
    """A display colour (#rrggbb) for a scene unit, never too dark to see."""
    if unit["s"] == 0 and unit["v"] == 0:
        level = 0.45 + 0.55 * unit["bright"] / 1000
        rgb = tuple(round(c * level) for c in temperature_rgb(unit["temp"] / 10))
    else:
        rgb = tuple(round(c * 255) for c in colorsys.hsv_to_rgb(
            unit["h"] / 360, unit["s"] / 1000, 0.45 + 0.55 * unit["v"] / 1000))
    return "#%02x%02x%02x" % rgb


@dataclass(frozen=True)
class Scene:
    label: str
    category: str  # work, relax or fun
    data: str


def _scene(number, label, category, *units):
    return Scene(label, category, encode_scene(number, units))


SCENES = {
    # work
    "work": Scene("Trabalho", "work", "020e0d0000000000000003e803e8"),
    "focus": _scene(0x08, "Foco profundo", "work", white_unit(100, 85)),
    "study": _scene(0x09, "Estudo", "work", white_unit(90, 70)),
    "read": Scene("Leitura", "work", "010e0d0000000000000003e801f4"),
    "meeting": _scene(0x0a, "Videochamada", "work", white_unit(80, 55)),
    "creative": _scene(0x0b, "Criatividade", "work", colour_unit(200, 25, 100)),
    "evening_work": _scene(0x0c, "Trabalho à noite", "work", white_unit(55, 25)),
    # relax
    "leisure": Scene("Lazer", "relax", "030e0d0000000000000001f401f4"),
    "dinner": _scene(0x0d, "Jantar", "relax", white_unit(40, 10)),
    "bedtime": _scene(0x0e, "Leitura na cama", "relax", white_unit(45, 0)),
    "cinema": _scene(0x0f, "Cinema", "relax", white_unit(10, 0)),
    "night": Scene("Noite", "relax", "000e0d0000000000000000c80000"),
    "sunset": _scene(0x10, "Pôr do sol", "relax",
                     colour_unit(25, 90, 90, FADE, SLOW), colour_unit(340, 70, 70, FADE, SLOW),
                     colour_unit(38, 95, 80, FADE, SLOW)),
    "candle": _scene(0x11, "Vela", "relax",
                     colour_unit(28, 100, 45, FADE, FAST), colour_unit(24, 100, 30, FADE, FAST),
                     colour_unit(30, 100, 50, FADE, FAST), colour_unit(26, 100, 35, FADE, FAST)),
    "fireplace": _scene(0x12, "Lareira", "relax",
                        colour_unit(12, 100, 70, FADE, MEDIUM), colour_unit(25, 100, 50, FADE, MEDIUM),
                        colour_unit(5, 95, 60, FADE, MEDIUM)),
    "ocean": _scene(0x13, "Oceano", "relax",
                    colour_unit(195, 90, 70, FADE, SLOW), colour_unit(210, 80, 60, FADE, SLOW),
                    colour_unit(180, 70, 65, FADE, SLOW)),
    "forest": _scene(0x14, "Floresta", "relax",
                     colour_unit(120, 70, 50, FADE, SLOW), colour_unit(95, 60, 60, FADE, SLOW),
                     colour_unit(140, 80, 45, FADE, SLOW)),
    "aurora": _scene(0x15, "Aurora", "relax",
                     colour_unit(150, 90, 60, FADE, SLOW), colour_unit(175, 80, 60, FADE, SLOW),
                     colour_unit(270, 70, 55, FADE, SLOW), colour_unit(300, 60, 50, FADE, SLOW)),
    "meditation": _scene(0x16, "Meditação", "relax",
                         colour_unit(265, 70, 20, FADE, SLOW), colour_unit(265, 70, 60, FADE, SLOW)),
    "spa": _scene(0x17, "Spa", "relax", colour_unit(170, 45, 45)),
    "moonlight": _scene(0x18, "Luar", "relax", colour_unit(225, 60, 15)),
    # fun (Tuya factory scenes and a party mode)
    "soft": Scene("Suave", "fun", "04464602007803e803e800000000464602007803e8000a00000000"),
    "rainbow": Scene("Arco-íris", "fun", "05464601000003e803e800000000464601007803e803e800000000"
                                         "46460100f003e803e800000000"),
    "shine": Scene("Piscar", "fun", "06464601000003e803e800000000464601007803e803e800000000"
                                    "46460100f003e803e800000000"),
    "gorgeous": Scene("Deslumbrante", "fun", "07464602000003e803e800000000464602007803e803e800000000"
                                             "46460200f003e803e800000000464602003d03e803e800000000"
                                             "46460200ae03e803e800000000464602011303e803e800000000"),
    "party": _scene(0x19, "Festa", "fun",
                    colour_unit(0, 100, 100, JUMP, FAST), colour_unit(300, 100, 100, JUMP, FAST),
                    colour_unit(230, 100, 100, JUMP, FAST), colour_unit(50, 100, 100, JUMP, FAST),
                    colour_unit(180, 100, 100, JUMP, FAST)),
}


class BulbError(Exception):
    pass


@dataclass
class BulbConfig:
    device_id: str
    ip: str
    local_key: str
    version: float = 3.5
    name: str = "Smart lamp"
    watts: float = DEFAULT_WATTS

    @classmethod
    def load(cls):
        """Read config from BULB_* env vars, falling back to a tinytuya devices.json."""
        label = os.environ.get("BULB_LABEL", "Smart lamp")
        watts = float(os.environ.get("BULB_WATTS", DEFAULT_WATTS))
        if os.environ.get("BULB_ID"):
            key = os.environ.get("BULB_KEY")
            key_file = os.environ.get("BULB_KEY_FILE")
            if not key and key_file:
                with open(key_file) as f:
                    key = f.read().strip()
            if not key:
                raise BulbError("BULB_KEY or BULB_KEY_FILE must be set")
            return cls(
                device_id=os.environ["BULB_ID"],
                ip=os.environ.get("BULB_IP", "Auto"),
                local_key=key,
                version=float(os.environ.get("BULB_VERSION", "3.5")),
                name=label,
                watts=watts,
            )

        path = os.environ.get("BULB_DEVICES_JSON", os.path.expanduser("~/devices.json"))
        name = os.environ.get("BULB_NAME", "Smart color")
        with open(path) as f:
            devices = json.load(f)
        dev = next((d for d in devices if d.get("name") == name), devices[0])
        return cls(
            device_id=dev["id"],
            ip=dev.get("ip") or "Auto",
            local_key=dev["key"],
            version=float(dev.get("version") or 3.5),
            name=label,
            watts=watts,
        )

    @classmethod
    def load_all(cls):
        """Every bulb to control: BULB_DEMO, BULBS_FILE (devices.json format) or load()."""
        demo = int(os.environ.get("BULB_DEMO") or 0)
        if demo:
            names = ["Sala", "Quarto", "Escritório", "Cozinha"]
            return [cls(f"demo{i + 1}", "demo", "", name=names[i % len(names)]) for i in range(demo)]
        path = os.environ.get("BULBS_FILE")
        if not path:
            return [cls.load()]
        with open(path) as f:
            devices = json.load(f)
        return [cls(
            device_id=d["id"],
            ip=d.get("ip") or "Auto",
            local_key=d["key"],
            version=float(d.get("version") or 3.5),
            name=d.get("name") or d["id"],
            watts=float(d.get("watts") or DEFAULT_WATTS),
        ) for d in devices if d.get("key")]


def parse_color(value):
    """Accept a colour name from COLOR_NAMES or #rrggbb; return (r, g, b)."""
    value = str(value).strip().lower()
    if value in COLOR_NAMES:
        return COLOR_NAMES[value]
    hex_value = value.lstrip("#")
    if len(hex_value) != 6:
        raise ValueError(f"invalid colour: {value}")
    try:
        return tuple(int(hex_value[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        raise ValueError(f"invalid colour: {value}") from None


def hsv_hex(hue, sat, val):
    """hue 0-360, sat and val 0-1000 -> DPS 24 string."""
    return "%04x%04x%04x" % (round(hue) % 360, round(sat), max(10, round(val)))


def state_from_dps(dps):
    """Translate raw DPS into the public state (scene ids are resolved by the caller)."""
    mode = dps.get("21")
    state = {
        "on": bool(dps.get("20")),
        "mode": mode,
        "temperature": round(dps.get("23", 0) / 10),
        "timer_seconds": dps.get("26", 0),
        "color": None,
        "scene_data": dps.get("25") if mode == "scene" else None,
    }
    if mode == "colour" and dps.get("24"):
        hsv = dps["24"]
        # in colour mode brightness is the V of HSV (0-1000)
        state["brightness"] = max(1, round(int(hsv[8:12], 16) / 10))
        # report the hue/saturation at full value so the UI shows the pure colour
        hue, sat = int(hsv[0:4], 16) / 360, int(hsv[4:8], 16) / 1000
        r, g, b = (round(c * 255) for c in colorsys.hsv_to_rgb(hue, sat, 1.0))
        state["color"] = "#%02x%02x%02x" % (r, g, b)
    else:
        state["brightness"] = max(1, round(dps.get("22", 0) / 10))
    return state


def _check_percent(value, name, low=0):
    if not low <= value <= 100:
        raise ValueError(f"{name} must be {low}-100")


class Bulb:
    def __init__(self, config: BulbConfig):
        if config.ip == "demo":
            self._device = DemoDevice()
        else:
            self._device = tinytuya.BulbDevice(config.device_id, config.ip, config.local_key)
            self._device.set_version(config.version)
            self._device.set_socketPersistent(False)
            self._device.set_socketTimeout(3)
            self._device.set_socketRetryLimit(2)
        # the bulb only handles one connection at a time
        self._lock = threading.Lock()

    def _call(self, fn, *args):
        with self._lock:
            result = fn(*args)
        if isinstance(result, dict) and "Error" in result:
            raise BulbError(f"{result.get('Error')} ({result.get('Err')})")
        return result

    def _set(self, values):
        self._call(self._device.set_multiple_values, values)

    def dps(self):
        result = self._call(self._device.status)
        if not isinstance(result, dict) or "dps" not in result:
            raise BulbError("no status from bulb")
        return result["dps"]

    def status(self):
        return state_from_dps(self.dps())

    def turn_on(self):
        self._call(self._device.set_value, 20, True)

    def turn_off(self):
        self._call(self._device.set_value, 20, False)

    def toggle(self):
        if self.status()["on"]:
            self.turn_off()
        else:
            self.turn_on()

    def set_color(self, value, brightness=None):
        """Colour by name or #rrggbb; brightness (percent) overrides the colour's own value."""
        r, g, b = parse_color(value)
        hue, sat, val = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        if brightness is not None:
            _check_percent(brightness, "brightness", 1)
            val = brightness / 100
        self._set({"20": True, "21": "colour", "24": hsv_hex(hue * 360, sat * 1000, val * 1000)})

    def set_brightness(self, percent):
        _check_percent(percent, "brightness", 1)
        dps = self.dps()
        if dps.get("21") == "colour" and dps.get("24"):
            hsv = dps["24"]
            self._set({"20": True, "24": hsv[:8] + "%04x" % max(10, percent * 10)})
        else:
            self._set({"20": True, "21": "white", "22": max(10, percent * 10)})

    def set_white(self, temperature=None, brightness=None):
        values = {"20": True, "21": "white"}
        if temperature is not None:
            _check_percent(temperature, "temperature")
            values["23"] = temperature * 10
        if brightness is not None:
            _check_percent(brightness, "brightness", 1)
            values["22"] = max(10, brightness * 10)
        self._set(values)

    def set_scene(self, scene_id):
        if scene_id not in SCENES:
            raise ValueError(f"unknown scene: {scene_id}. Options: {', '.join(SCENES)}")
        self.set_scene_data(SCENES[scene_id].data)

    def set_scene_data(self, data):
        self._set({"20": True, "21": "scene", "25": data})

    def set_timer(self, minutes):
        if minutes < 0:
            raise ValueError("timer must be >= 0")
        self._call(self._device.set_value, 26, int(minutes * 60))

    def restore(self, dps, power=None):
        """Put back a snapshot taken with dps(); `power` overrides its on/off state."""
        mode = dps.get("21")
        keys = {"white": ("22", "23"), "colour": ("24",), "scene": ("25",)}.get(mode, ())
        values = {"21": mode, **{k: dps[k] for k in keys if k in dps}} if mode else {}
        values["20"] = bool(dps.get("20")) if power is None else power
        self._set(values)


class DemoDevice:
    """In-memory stand-in for a bulb (BULB_DEMO=n), for trying the app without hardware."""

    def __init__(self):
        self._dps = {"20": False, "21": "white", "22": 800, "23": 500,
                     "24": "000003e803e8", "25": SCENES["night"].data, "26": 0}
        self._timer_end = None

    def status(self):
        time.sleep(0.03)
        if self._timer_end and time.time() >= self._timer_end:
            self._dps["20"], self._timer_end = False, None
        dps = dict(self._dps)
        dps["26"] = max(0, round(self._timer_end - time.time())) if self._timer_end else 0
        return {"dps": dps}

    def set_value(self, index, value):
        return self.set_multiple_values({str(index): value})

    def set_multiple_values(self, values):
        time.sleep(0.03)
        for key, value in values.items():
            if str(key) == "26":
                self._timer_end = time.time() + value if value else None
            else:
                self._dps[str(key)] = value
        return {"dps": values}
