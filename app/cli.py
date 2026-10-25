"""Command-line client for the bulb.

Usage: python -m app.cli [--target ID] <command> [arg]

  status
  on | off | toggle
  color <name|#rrggbb>        e.g. color red, color #ff8800
  brightness <1-100>
  white [temperature 0-100]   0 = warm, 100 = cool
  scene <name>                e.g. focus, ocean, candle, night (see `scenes`)
  scenes                      list scene ids
  timer <minutes>             turn off after N minutes (0 cancels)

Server only (needs SMARTBULB_URL):
  favorite <id>               apply a saved favourite
  undo                        go back to the previous light
  flash [#rrggbb] [times]     blink to get attention, then restore
  wake | sleep [minutes]      gradual sunrise / dim and turn off
  pomodoro [work] [rest] [cycles]
  stop                        stop the running routine

Portuguese aliases also work: cor, brilho, branco, cena, ligar, desligar.

Two modes:
- Server: set SMARTBULB_URL (e.g. http://server:8080) and SMARTBULB_TOKEN (an API
  token from "Minha conta" in the web UI). Goes through the app, so routines,
  history and live updates in the browser all work. --target picks a bulb or group.
- Direct (no SMARTBULB_URL): talks to the bulb over the LAN using BULB_ID/BULB_IP/
  BULB_KEY env vars, or the devices.json written by `tinytuya wizard`
  (default ~/devices.json, override with BULB_DEVICES_JSON).
"""
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

from .bulb import SCENES, Bulb, BulbConfig, BulbError

COMMAND_ALIASES = {"cor": "color", "brilho": "brightness", "branco": "white", "cena": "scene",
                   "ligar": "on", "desligar": "off"}
SERVER_ONLY = ("favorite", "undo", "flash", "wake", "sleep", "pomodoro", "stop")

# accept the Portuguese scene labels (without accents) as well as the scene ids
_PLAIN = str.maketrans("áàâãéêíóôõúç", "aaaaeeiooouc")
SCENE_ALIASES = {
    scene.label.lower().translate(_PLAIN): scene_id for scene_id, scene in SCENES.items()
}


def scene_id(name):
    name = name.lower()
    return SCENE_ALIASES.get(name.translate(_PLAIN), name)


def format_status(state):
    if state["mode"] == "colour":
        detail = f"color: {state['color']}"
    elif state["mode"] == "scene":
        detail = f"scene: {state.get('scene') or '?'}"
    else:
        detail = f"temperature: {state['temperature']}%"
    line = (f"on: {state['on']}  mode: {state['mode']}  brightness: {state['brightness']}%  "
            f"{detail}  timer: {state['timer_seconds']}s")
    if state.get("effect"):
        line += f"  routine: {state['effect']['label']}"
    return line


class Server:
    def __init__(self, url, token, target):
        self._url = url.rstrip("/")
        self._token = token
        self._target = target

    def call(self, method, path, body=None):
        if self._target:
            path += ("&" if "?" in path else "?") + "target=" + urllib.parse.quote(self._target)
        request = urllib.request.Request(
            self._url + path, method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self._token}"},
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                data = response.read()
                return json.loads(data) if data else None
        except urllib.error.HTTPError as exc:
            try:
                detail = json.loads(exc.read()).get("detail")
            except Exception:
                detail = exc.reason
            raise BulbError(f"{exc.code}: {detail}") from None

    def run(self, command, args):
        arg = args[0] if args else None
        post = lambda path, body=None: self.call("POST", path, body)  # noqa: E731
        if command == "status":
            return self.call("GET", "/api/status")
        if command == "scenes":
            return [f"{s['id']:<14} {s['label']} ({s['category']})" for s in self.call("GET", "/api/info")["scenes"]]
        routes = {
            "on": lambda: post("/api/power", {"on": True}),
            "off": lambda: post("/api/power", {"on": False}),
            "toggle": lambda: post("/api/toggle"),
            "color": lambda: post("/api/color", {"color": arg}),
            "brightness": lambda: post("/api/brightness", {"value": int(arg)}),
            "white": lambda: post("/api/white", {"temperature": int(arg) if arg else None}),
            "scene": lambda: post("/api/scene", {"scene": scene_id(arg)}),
            "favorite": lambda: post("/api/favorite", {"favorite": arg}),
            "timer": lambda: post("/api/timer", {"minutes": float(arg)}),
            "undo": lambda: post("/api/undo"),
            "flash": lambda: post("/api/effects/flash", {"color": arg or "#ff0000",
                                                         "times": int(args[1]) if len(args) > 1 else 3}),
            "wake": lambda: post("/api/effects/fade", {"direction": "wake", "minutes": int(arg or 15)}),
            "sleep": lambda: post("/api/effects/fade", {"direction": "sleep", "minutes": int(arg or 15)}),
            "pomodoro": lambda: post("/api/effects/pomodoro", dict(zip(("work", "rest", "cycles"), map(int, args)))),
            "stop": lambda: self.call("DELETE", "/api/effects"),
        }
        if command not in routes:
            sys.exit(__doc__)
        return routes[command]()


def run_direct(command, args):
    arg = args[0] if args else None
    if command in SERVER_ONLY:
        sys.exit(f"`{command}` needs the server: set SMARTBULB_URL and SMARTBULB_TOKEN")
    if command == "scenes":
        return [f"{sid:<14} {s.label} ({s.category})" for sid, s in SCENES.items()]
    bulb = Bulb(BulbConfig.load())
    if command == "status":
        state = bulb.status()
        data = state.pop("scene_data")
        state["scene"] = next((k for k, s in SCENES.items() if s.data == data), None)
        return state
    actions = {
        "on": bulb.turn_on,
        "off": bulb.turn_off,
        "toggle": bulb.toggle,
        "color": lambda: bulb.set_color(arg),
        "brightness": lambda: bulb.set_brightness(int(arg)),
        "white": lambda: bulb.set_white(int(arg) if arg is not None else None),
        "scene": lambda: bulb.set_scene(scene_id(arg)),
        "timer": lambda: bulb.set_timer(float(arg)),
    }
    if command not in actions:
        sys.exit(__doc__)
    actions[command]()
    return None


def main(argv):
    target = None
    if argv[:1] == ["--target"] and len(argv) > 1:
        target, argv = argv[1], argv[2:]
    if not argv:
        sys.exit(__doc__)
    command = COMMAND_ALIASES.get(argv[0], argv[0])
    args = argv[1:]
    needs_arg = ("color", "brightness", "scene", "timer", "favorite")
    if command in needs_arg and not args:
        sys.exit(__doc__)

    url = os.environ.get("SMARTBULB_URL")
    try:
        if url:
            token = os.environ.get("SMARTBULB_TOKEN")
            if not token:
                sys.exit("Set SMARTBULB_TOKEN (create one under Minha conta in the web UI)")
            result = Server(url, token, target).run(command, args)
        else:
            result = run_direct(command, args)
    except (BulbError, ValueError, OSError) as exc:
        sys.exit(f"Error: {exc}")

    if isinstance(result, list):
        print("\n".join(result))
    elif command == "status" and result:
        print(format_status(result))


if __name__ == "__main__":
    main(sys.argv[1:])
