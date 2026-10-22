#!/usr/bin/env python3
"""Controle local da lâmpada Tuya (Smart lamp) sem nuvem.

Uso:
  lampada.py status
  lampada.py on | off | toggle
  lampada.py cor <nome|#rrggbb>     ex: cor vermelho, cor #ff8800
  lampada.py brilho <1-100>
  lampada.py branco [temp 0-100]    0 = quente, 100 = fria
  lampada.py cena <nome>            noite, leitura, trabalho, lazer, suave, arco-iris, piscar, deslumbrante
  lampada.py timer <minutos>        desliga depois de N minutos (0 cancela)

Configuração: variáveis BULB_ID/BULB_IP/BULB_KEY, ou o devices.json do
`tinytuya wizard` (padrão ~/devices.json, ou BULB_DEVICES_JSON=/caminho).
"""
import sys

from app.bulb import SCENES, Bulb, BulbConfig, BulbError

# accept the Portuguese labels (without accents) as well as the scene ids
SCENE_ALIASES = {
    label.lower().replace("í", "i"): scene_id for scene_id, (label, _) in SCENES.items()
}


def main(argv):
    if not argv:
        sys.exit(__doc__)
    command, args = argv[0], argv[1:]
    bulb = Bulb(BulbConfig.load())

    try:
        if command == "status":
            state = bulb.status()
            if state["mode"] == "colour":
                extra = f"cor: {state['color']}"
            elif state["mode"] == "scene":
                extra = f"cena: {SCENES[state['scene']][0] if state['scene'] else '?'}"
            else:
                extra = f"temp: {state['temperature']}%"
            print(f"ligada: {state['on']}  modo: {state['mode']}  brilho: {state['brightness']}%  "
                  f"{extra}  timer: {state['timer_seconds']}s")
        elif command == "on":
            bulb.turn_on()
        elif command == "off":
            bulb.turn_off()
        elif command == "toggle":
            bulb.toggle()
        elif command == "cor" and args:
            bulb.set_color(args[0])
        elif command == "brilho" and args:
            bulb.set_brightness(int(args[0]))
        elif command == "branco":
            bulb.set_white(int(args[0]) if args else None)
        elif command == "cena" and args:
            bulb.set_scene(SCENE_ALIASES.get(args[0].lower(), args[0].lower()))
        elif command == "timer" and args:
            bulb.set_timer(float(args[0]))
        else:
            sys.exit(__doc__)
    except (BulbError, ValueError) as exc:
        sys.exit(f"Erro: {exc}")


if __name__ == "__main__":
    main(sys.argv[1:])
