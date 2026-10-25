"""Scenes (built-in and user-made) and saved favourites."""
import colorsys
import secrets

from .bulb import (FADE, JUMP, SCENES, STATIC, colour_unit, decode_scene, duration, encode_scene, parse_color,
                   smooth_units, unit_preview)

TRANSITIONS = {"static": STATIC, "jump": JUMP, "fade": FADE}
CUSTOM_SCENE_BASE = 0x20  # user scenes use 0x20-0x5f; built-in ones are below and above
MAX_FAVORITES = 48
MAX_SCENES = 32


def _preview(data):
    units = decode_scene(data)
    transition = {STATIC: "static", JUMP: "jump", FADE: "fade"}.get(units[0]["mode"] if units else 0, "static")
    return {"colors": [unit_preview(u) for u in units], "transition": transition}


def custom_scene_data(scene):
    """Encode a user scene: colours, transition and speed (1 slow - 100 fast)."""
    mode = TRANSITIONS[scene["transition"]]
    speed = duration(101 - scene["speed"])
    colours = []
    for colour in scene["colors"][:1] if mode == STATIC else scene["colors"]:
        r, g, b = parse_color(colour)
        hue, sat, _ = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        colours.append((hue * 360, sat * 100, scene["brightness"]))
    if mode == FADE and len(colours) > 1:
        return encode_scene(scene["number"], smooth_units(colours, speed))
    return encode_scene(scene["number"], [colour_unit(h, s, v, mode, speed) for h, s, v in colours])


class Library:
    def __init__(self, store, broker):
        self._store = store
        self._broker = broker
        store.data.setdefault("favorites", [])
        store.data.setdefault("scenes", [])

    # --- scenes

    def scenes(self):
        result = [{"id": sid, "label": s.label, "category": s.category, "custom": False, **_preview(s.data)}
                  for sid, s in SCENES.items()]
        with self._store.lock:
            for scene in self._store.data["scenes"]:
                result.append({"id": scene["id"], "label": scene["name"], "category": "mine", "custom": True,
                               **_preview(custom_scene_data(scene))})
        return result

    def scene_label(self, scene_id):
        return next((s["label"] for s in self.scenes() if s["id"] == scene_id), scene_id)

    def scene_data(self, scene_id):
        if scene_id in SCENES:
            return SCENES[scene_id].data
        scene = self._custom(scene_id)
        if not scene:
            raise ValueError(f"cena desconhecida: {scene_id}")
        return custom_scene_data(scene)

    def _custom(self, scene_id):
        return next((s for s in self._store.data["scenes"] if s["id"] == scene_id), None)

    def lookup(self, data):
        """Scene id for raw DPS 25 data (exact match first, then by scene number)."""
        if not data:
            return None
        candidates = {sid: s.data for sid, s in SCENES.items()}
        with self._store.lock:
            candidates.update({s["id"]: custom_scene_data(s) for s in self._store.data["scenes"]})
        exact = next((sid for sid, d in candidates.items() if d == data), None)
        return exact or next((sid for sid, d in candidates.items() if d[:2] == data[:2]), None)

    def save_scene(self, name, colors, transition, speed, brightness):
        name = (name or "").strip()[:30]
        if not name:
            raise ValueError("dê um nome à cena")
        if transition not in TRANSITIONS:
            raise ValueError("transição deve ser static, jump ou fade")
        if not 1 <= len(colors) <= 8:
            raise ValueError("use de 1 a 8 cores")
        colors = ["#%02x%02x%02x" % parse_color(c) for c in colors]
        if not (1 <= speed <= 100 and 1 <= brightness <= 100):
            raise ValueError("velocidade e brilho vão de 1 a 100")
        with self._store.lock:
            scenes = self._store.data["scenes"]
            if len(scenes) >= MAX_SCENES:
                raise ValueError(f"limite de {MAX_SCENES} cenas")
            used = {s["number"] for s in scenes}
            number = next(n for n in range(CUSTOM_SCENE_BASE, 0x100) if n not in used)
            scene = {"id": "c" + secrets.token_hex(3), "name": name, "colors": colors,
                     "transition": transition, "speed": speed, "brightness": brightness, "number": number}
            scenes.append(scene)
            self._store.save()
        self._broker.publish("library")
        return scene

    def delete_scene(self, scene_id):
        with self._store.lock:
            scene = self._custom(scene_id)
            if not scene:
                raise LookupError("cena não encontrada")
            self._store.data["scenes"].remove(scene)
            self._store.save()
        self._broker.publish("library")

    # --- favourites: a saved look (white, colour or scene)

    def favorites(self):
        with self._store.lock:
            return [dict(f) for f in self._store.data["favorites"]]

    def favorite(self, favorite_id):
        found = next((f for f in self.favorites() if f["id"] == favorite_id), None)
        if not found:
            raise ValueError(f"favorito desconhecido: {favorite_id}")
        return found

    def save_favorite(self, name, state):
        name = (name or "").strip()[:30]
        if not name:
            raise ValueError("dê um nome ao favorito")
        mode = state.get("mode")
        if mode == "colour":
            look = {"mode": "colour", "color": state["color"], "brightness": state["brightness"]}
        elif mode == "scene" and state.get("scene"):
            look = {"mode": "scene", "scene": state["scene"]}
        else:
            look = {"mode": "white", "temperature": state["temperature"], "brightness": state["brightness"]}
        with self._store.lock:
            if len(self._store.data["favorites"]) >= MAX_FAVORITES:
                raise ValueError(f"limite de {MAX_FAVORITES} favoritos")
            favorite = {"id": "f" + secrets.token_hex(3), "name": name, **look}
            self._store.data["favorites"].append(favorite)
            self._store.save()
        self._broker.publish("library")
        return favorite

    def delete_favorite(self, favorite_id):
        with self._store.lock:
            before = len(self._store.data["favorites"])
            self._store.data["favorites"] = [f for f in self._store.data["favorites"] if f["id"] != favorite_id]
            if len(self._store.data["favorites"]) == before:
                raise LookupError("favorito não encontrado")
            self._store.save()
        self._broker.publish("library")

    def apply_favorite(self, bulb, favorite):
        if favorite["mode"] == "colour":
            bulb.set_color(favorite["color"], favorite["brightness"])
        elif favorite["mode"] == "scene":
            bulb.set_scene_data(self.scene_data(favorite["scene"]))
        else:
            bulb.set_white(favorite["temperature"], favorite["brightness"])
