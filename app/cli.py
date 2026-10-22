"""Command-line client for the bulb (talks to it directly over the LAN).

Usage: python -m app.cli <command> [arg]

  status
  on | off | toggle
  color <name|#rrggbb>        e.g. color red, color #ff8800
  brightness <1-100>
  white [temperature 0-100]   0 = warm, 100 = cool
  scene <name>                night, read, work, leisure, soft, rainbow, shine, gorgeous
  timer <minutes>             turn off after N minutes (0 cancels)

Portuguese aliases also work: cor, brilho, branco, cena (e.g. `cena noite`).

Config: BULB_ID/BULB_IP/BULB_KEY env vars, or the devices.json written by
`tinytuya wizard` (default ~/devices.json, override with BULB_DEVICES_JSON).
"""
import sys

from .bulb import SCENES, Bulb, BulbConfig, BulbError

COMMAND_ALIASES = {"cor": "color", "brilho": "brightness", "branco": "white", "cena": "scene"}

# accept the Portuguese scene labels (without accents) as well as the scene ids
SCENE_ALIASES = {
    label.lower().replace("í", "i"): scene_id for scene_id, (label, _) in SCENES.items()
}


def format_status(state):
    if state["mode"] == "colour":
        detail = f"color: {state['color']}"
    elif state["mode"] == "scene":
        detail = f"scene: {state['scene'] or '?'}"
    else:
        detail = f"temperature: {state['temperature']}%"
    return (f"on: {state['on']}  mode: {state['mode']}  brightness: {state['brightness']}%  "
            f"{detail}  timer: {state['timer_seconds']}s")


def main(argv):
    if not argv:
        sys.exit(__doc__)
    command = COMMAND_ALIASES.get(argv[0], argv[0])
    arg = argv[1] if len(argv) > 1 else None
    if command not in ("status", "on", "off", "toggle", "white") and arg is None:
        sys.exit(__doc__)

    try:
        bulb = Bulb(BulbConfig.load())
        if command == "status":
            print(format_status(bulb.status()))
        elif command == "on":
            bulb.turn_on()
        elif command == "off":
            bulb.turn_off()
        elif command == "toggle":
            bulb.toggle()
        elif command == "color":
            bulb.set_color(arg)
        elif command == "brightness":
            bulb.set_brightness(int(arg))
        elif command == "white":
            bulb.set_white(int(arg) if arg is not None else None)
        elif command == "scene":
            bulb.set_scene(SCENE_ALIASES.get(arg.lower(), arg.lower()))
        elif command == "timer":
            bulb.set_timer(float(arg))
        else:
            sys.exit(__doc__)
    except (BulbError, ValueError, OSError) as exc:
        sys.exit(f"Error: {exc}")


if __name__ == "__main__":
    main(sys.argv[1:])
