"""Local control of a Tuya bulb (no cloud).

DPS map for this bulb: 20 power, 21 mode (white/colour/scene),
22 white brightness (10-1000), 23 colour temperature (0-1000),
24 colour HSV hex (hhhhssssvvvv), 26 countdown in seconds.
"""
import colorsys
import json
import os
import threading
from dataclasses import dataclass

import tinytuya

COLOR_NAMES = {
    "vermelho": (255, 0, 0),
    "verde": (0, 255, 0),
    "azul": (0, 0, 255),
    "amarelo": (255, 200, 0),
    "laranja": (255, 100, 0),
    "roxo": (140, 0, 255),
    "rosa": (255, 0, 120),
    "ciano": (0, 255, 255),
    "branco": (255, 255, 255),
}

# Built-in Tuya scenes (DPS 25, scene_data v2). Each unit is:
# interval(2) duration(2) mode(2: 00 static, 01 jump, 02 fade) H(4) S(4) V(4) bright(4) temp(4)
SCENES = {
    "night": ("Noite", "000e0d0000000000000000c80000"),
    "read": ("Leitura", "010e0d0000000000000003e801f4"),
    "work": ("Trabalho", "020e0d0000000000000003e803e8"),
    "leisure": ("Lazer", "030e0d0000000000000001f401f4"),
    "soft": ("Suave", "04464602007803e803e800000000464602007803e8000a00000000"),
    "rainbow": ("Arco-íris", "05464601000003e803e800000000464601007803e803e800000000"
                             "46460100f003e803e800000000"),
    "shine": ("Piscar", "06464601000003e803e800000000464601007803e803e800000000"
                        "46460100f003e803e800000000"),
    "gorgeous": ("Deslumbrante", "07464602000003e803e800000000464602007803e803e800000000"
                                 "46460200f003e803e800000000464602003d03e803e800000000"
                                 "46460200ae03e803e800000000464602011303e803e800000000"),
}


class BulbError(Exception):
    pass


@dataclass
class BulbConfig:
    device_id: str
    ip: str
    local_key: str
    version: float = 3.5

    @classmethod
    def load(cls):
        """Read config from BULB_* env vars, falling back to a tinytuya devices.json."""
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
        )


def parse_color(value):
    """Accept a colour name from COLOR_NAMES or #rrggbb; return (r, g, b)."""
    value = value.strip().lower()
    if value in COLOR_NAMES:
        return COLOR_NAMES[value]
    hex_value = value.lstrip("#")
    if len(hex_value) != 6:
        raise ValueError(f"invalid colour: {value}")
    try:
        return tuple(int(hex_value[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        raise ValueError(f"invalid colour: {value}") from None


class Bulb:
    def __init__(self, config: BulbConfig):
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

    def status(self):
        dps = self._call(self._device.status)["dps"]
        mode = dps.get("21")
        state = {
            "on": bool(dps.get("20")),
            "mode": mode,
            "temperature": round(dps.get("23", 0) / 10),
            "timer_seconds": dps.get("26", 0),
            "color": None,
            "scene": None,
        }
        if mode == "scene":
            data = dps.get("25", "")
            state["scene"] = next((k for k, (_, d) in SCENES.items() if d[:2] == data[:2]), None)
        if mode == "colour" and dps.get("24"):
            hsv = dps["24"]
            # in colour mode brightness is the V of HSV (0-1000)
            state["brightness"] = round(int(hsv[8:12], 16) / 10)
            # report the hue/saturation at full value so the UI shows the pure colour
            hue, sat = int(hsv[0:4], 16) / 360, int(hsv[4:8], 16) / 1000
            r, g, b = (round(c * 255) for c in colorsys.hsv_to_rgb(hue, sat, 1.0))
            state["color"] = "#%02x%02x%02x" % (r, g, b)
        else:
            state["brightness"] = round(dps.get("22", 0) / 10)
        return state

    def turn_on(self):
        self._call(self._device.turn_on)

    def turn_off(self):
        self._call(self._device.turn_off)

    def toggle(self):
        if self.status()["on"]:
            self.turn_off()
        else:
            self.turn_on()

    def set_color(self, value):
        r, g, b = parse_color(value)
        self.turn_on()
        self._call(self._device.set_colour, r, g, b)

    def set_brightness(self, percent):
        if not 1 <= percent <= 100:
            raise ValueError("brightness must be 1-100")
        self.turn_on()
        self._call(self._device.set_brightness_percentage, percent)

    def set_white(self, temperature=None):
        self.turn_on()
        self._call(self._device.set_mode, "white")
        if temperature is not None:
            if not 0 <= temperature <= 100:
                raise ValueError("temperature must be 0-100")
            self._call(self._device.set_colourtemp_percentage, temperature)

    def set_scene(self, scene_id):
        if scene_id not in SCENES:
            raise ValueError(f"unknown scene: {scene_id}. Options: {', '.join(SCENES)}")
        self.turn_on()
        self._call(self._device.set_multiple_values, {"21": "scene", "25": SCENES[scene_id][1]})

    def set_timer(self, minutes):
        if minutes < 0:
            raise ValueError("timer must be >= 0")
        self._call(self._device.set_value, 26, int(minutes * 60))
