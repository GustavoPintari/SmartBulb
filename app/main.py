import asyncio
import base64
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from zoneinfo import ZoneInfo

import tzlocal
from apscheduler.triggers.interval import IntervalTrigger
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .activity import ActivityLog
from .auth import ROLE_RANK, Accounts, LoginThrottle
from .bulb import BulbConfig, BulbError, parse_color
from .devices import Registry
from .effects import Effects
from .events import Broker
from .library import Library, custom_scene_data
from .schedules import ScheduleStore
from .store import JsonStore
from .sun import Sun

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
log = logging.getLogger("smartbulb")

APP_NAME = "SmartBulb"
STATIC_DIR = Path(__file__).parent / "static"
DATA_DIR = os.environ.get("DATA_DIR", "/data")
SESSION_COOKIE = "sb_session"
PUBLIC_PATHS = {"/login", "/healthz", "/api/login"}


def read_secret(name):
    """Read NAME from the environment, or from the file at NAME_FILE (podman secrets)."""
    if os.environ.get(name):
        return os.environ[name]
    path = os.environ.get(f"{name}_FILE")
    if path:
        with open(path) as f:
            return f.read().strip()
    return None


def data_file(name, default):
    return JsonStore(os.path.join(DATA_DIR, name), default)


def env_location():
    lat, lon = os.environ.get("LATITUDE"), os.environ.get("LONGITUDE")
    return {"lat": float(lat), "lon": float(lon)} if lat and lon else None


TIMEZONE = ZoneInfo(os.environ["TZ"]) if os.environ.get("TZ") else tzlocal.get_localzone()
AUTH_DISABLED = os.environ.get("AUTH_DISABLED") == "1"

accounts = Accounts(data_file("users.json", {}))
accounts.bootstrap(os.environ.get("APP_USER", "admin"), read_secret("APP_PASSWORD"))
if not accounts.has_users() and not AUTH_DISABLED:
    raise SystemExit("Set APP_PASSWORD (or APP_PASSWORD_FILE) to create the first admin, "
                     "or AUTH_DISABLED=1 for a trusted network")
throttle = LoginThrottle()

broker = Broker()
settings = data_file("settings.json", {})
registry = Registry(BulbConfig.load_all(), data_file("groups.json", []), data_file("stats.json", {}),
                    broker, float(os.environ.get("POLL_SECONDS", "5")))
library = Library(data_file("library.json", {}), broker)
registry.scene_lookup = library.lookup
activity = ActivityLog(data_file("activity.json", []), broker, TIMEZONE)
sun = Sun(settings, TIMEZONE, env_location())
effects = Effects(registry, broker, settings, sun, activity, TIMEZONE)


# --- one entry point for every action (web UI, schedules, MQTT)

LOOK_ACTIONS = {"color", "white", "brightness", "scene", "favorite"}


def perform(actor, target, action, value=None, brightness=None):
    devices = registry.resolve(target)
    detail = None
    if action == "on":
        point = effects.circadian_point()

        def fn(device):
            if effects.circadian_enabled(device.id):
                device.bulb.set_white(*point)
            else:
                device.bulb.turn_on()
    elif action == "off":
        def fn(device):
            device.bulb.turn_off()
    elif action == "color":
        value = "#%02x%02x%02x" % parse_color(value)
        detail = value if brightness is None else f"{value} · {brightness}%"

        def fn(device):
            device.bulb.set_color(value, brightness)
    elif action == "white":
        value = None if value in (None, "") else int(value)
        detail = " · ".join(p for p in (
            value is not None and f"temperatura {value}%", brightness and f"brilho {brightness}%") if p) or None

        def fn(device):
            device.bulb.set_white(value, brightness)
    elif action == "brightness":
        value = int(value)
        detail = f"{value}%"

        def fn(device):
            device.bulb.set_brightness(value)
    elif action == "scene":
        data = library.scene_data(value)
        detail = library.scene_label(value)

        def fn(device):
            device.bulb.set_scene_data(data)
    elif action == "favorite":
        favorite = library.favorite(value)
        detail = favorite["name"]

        def fn(device):
            library.apply_favorite(device.bulb, favorite)
    elif action == "timer":
        value = float(value)
        detail = f"{value:g} min" if value else "cancelado"

        def fn(device):
            device.bulb.set_timer(value)
    elif action in ("wake", "sleep"):
        effects.disable_circadian(devices)
        effects.fade(devices, action, int(value or 15))
        activity.add(actor, action, registry.target_name(target), f"{int(value or 15)} min")
        return
    elif action == "circadian":
        effects.set_circadian(devices, True)
        activity.add(actor, "circadian", registry.target_name(target), "ativado")
        return
    else:
        raise ValueError(f"unknown action: {action}")

    if action != "timer":
        effects.stop(devices)
    if action in LOOK_ACTIONS:
        effects.disable_circadian(devices)
    registry.apply(devices, fn, remember=action != "timer")
    activity.add(actor, action, registry.target_name(target), detail)


def check_reference(kind, value):
    if kind == "scene":
        library.scene_data(value)
    elif kind == "favorite":
        library.favorite(value)
    elif kind == "target" and value:
        registry.resolve(value)
    elif kind == "sun" and not sun.location:
        raise ValueError("defina a localização em Administração para usar nascer/pôr do sol")


schedules = ScheduleStore(
    os.path.join(DATA_DIR, "schedules.json"), TIMEZONE,
    lambda item: perform("agendamento", item["target"], item["action"], item["value"]),
    sun, broker,
)


def start_mqtt():
    host = os.environ.get("MQTT_HOST")
    if not host:
        return None
    try:
        from .mqtt import MqttBridge
        bridge = MqttBridge(
            {"host": host, "port": os.environ.get("MQTT_PORT"), "user": os.environ.get("MQTT_USER"),
             "password": read_secret("MQTT_PASSWORD"), "prefix": os.environ.get("MQTT_PREFIX"),
             "discovery_prefix": os.environ.get("MQTT_DISCOVERY_PREFIX")},
            registry, library,
            lambda device_id, action, value=None, **extra: perform("Home Assistant", device_id, action, value, **extra),
        )
        bridge.start()
        return bridge
    except Exception:
        log.exception("MQTT bridge disabled")
        return None


bridge = None


@asynccontextmanager
async def lifespan(app):
    global bridge
    registry.start()
    schedules.start()
    schedules.scheduler.add_job(effects.circadian_tick, IntervalTrigger(minutes=2), id="_circadian")
    schedules.scheduler.add_job(effects.presence_tick, IntervalTrigger(minutes=10), id="_presence")
    bridge = start_mqtt()
    yield
    if bridge:
        bridge.stop()
    schedules.shutdown()
    registry.stop()


app = FastAPI(title=APP_NAME, lifespan=lifespan, docs_url=None, redoc_url=None)


# --- authentication: session cookie (web), bearer token or HTTP Basic (scripts)

async def current_user(request: Request):
    if AUTH_DISABLED:
        return {"name": "admin", "role": "admin"}
    token = request.cookies.get(SESSION_COOKIE)
    if token and (user := accounts.session_user(token)):
        return user
    header = request.headers.get("authorization", "")
    if header.startswith("Bearer "):
        return accounts.token_user(header[7:].strip())
    if header.startswith("Basic "):
        address = request.client.host if request.client else "?"
        if throttle.wait_time(address):
            return None
        try:
            name, _, password = base64.b64decode(header[6:]).decode().partition(":")
        except Exception:
            return None
        user = await run_in_threadpool(accounts.authenticate, name, password)
        throttle.succeeded(address) if user else throttle.failed(address)
        return user
    return None


@app.middleware("http")
async def authenticate(request: Request, call_next):
    path = request.url.path
    request.state.user = await current_user(request)
    if path.startswith("/static/"):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-cache"  # revalidate so updates show up after a deploy
        return response
    if request.state.user or path in PUBLIC_PATHS:
        return await call_next(request)
    if path.startswith("/api/"):
        return JSONResponse(status_code=401, content={"detail": "sessão expirada, entre novamente"})
    return RedirectResponse("/login", status_code=303)


def role(minimum):
    def check(request: Request):
        user = request.state.user
        if ROLE_RANK[user["role"]] < ROLE_RANK[minimum]:
            raise HTTPException(403, "sem permissão para isso")
        return user
    return Depends(check)


GUEST, MEMBER, ADMIN = role("guest"), role("user"), role("admin")


@app.exception_handler(BulbError)
async def bulb_error(request, exc):
    return JSONResponse(status_code=502, content={"detail": f"lâmpada sem resposta: {exc}"})


@app.exception_handler(ValueError)
async def value_error(request, exc):
    return JSONResponse(status_code=422, content={"detail": str(exc)})


@app.exception_handler(LookupError)
async def lookup_error(request, exc):
    return JSONResponse(status_code=404, content={"detail": str(exc).strip("'")})


# --- request bodies

class Login(BaseModel):
    username: str
    password: str
    remember: bool = True


class PasswordChange(BaseModel):
    current: str
    new: str


class NewUser(BaseModel):
    username: str
    password: str
    role: str = "user"


class UserUpdate(BaseModel):
    password: str | None = None
    role: str | None = None


class TokenRequest(BaseModel):
    label: str = ""


class Prefs(BaseModel):
    accent: str | None = None
    hidden_scenes: list[str] = []


class Power(BaseModel):
    on: bool


class Color(BaseModel):
    color: str
    brightness: int | None = Field(default=None, ge=1, le=100)


class Brightness(BaseModel):
    value: int = Field(ge=1, le=100)


class White(BaseModel):
    temperature: int | None = Field(default=None, ge=0, le=100)
    brightness: int | None = Field(default=None, ge=1, le=100)


class SceneChoice(BaseModel):
    scene: str


class FavoriteChoice(BaseModel):
    favorite: str


class Timer(BaseModel):
    minutes: float = Field(ge=0, le=24 * 60)


class Fade(BaseModel):
    direction: str
    minutes: int = Field(default=15, ge=1, le=120)


class Pomodoro(BaseModel):
    work: int = Field(default=25, ge=1, le=120)
    rest: int = Field(default=5, ge=1, le=60)
    cycles: int = Field(default=4, ge=1, le=12)


class Flash(BaseModel):
    color: str = "#ff0000"
    times: int = Field(default=3, ge=1, le=10)


class Toggle(BaseModel):
    enabled: bool


class Presence(BaseModel):
    enabled: bool
    target: str | None = None


class Location(BaseModel):
    lat: float | None = Field(default=None, ge=-90, le=90)
    lon: float | None = Field(default=None, ge=-180, le=180)


class Group(BaseModel):
    name: str
    members: list[str]


class Favorite(BaseModel):
    name: str


class CustomScene(BaseModel):
    name: str = "Prévia"
    colors: list[str]
    transition: str = "fade"
    speed: int = Field(default=50, ge=1, le=100)
    brightness: int = Field(default=100, ge=1, le=100)


class Schedule(BaseModel):
    time: str = "00:00"
    trigger: str = "time"
    offset: int = 0
    days: list[int] = list(range(7))
    action: str
    value: str | int | None = None
    target: str | None = None
    enabled: bool = True


# --- pages

@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/login")
def login_page(request: Request):
    if request.state.user:
        return RedirectResponse("/", status_code=303)
    return FileResponse(STATIC_DIR / "login.html", headers={"Cache-Control": "no-cache"})


# --- session and account

@app.post("/api/login")
def login(body: Login, request: Request):
    address = request.client.host if request.client else "?"
    wait = throttle.wait_time(address)
    if wait:
        raise HTTPException(429, f"Muitas tentativas. Tente de novo em {wait} s.")
    user = accounts.authenticate(body.username, body.password)
    if not user:
        throttle.failed(address)
        raise HTTPException(401, "Usuário ou senha incorretos")
    throttle.succeeded(address)
    days = 30 if body.remember else 1
    response = JSONResponse(user)
    response.set_cookie(SESSION_COOKIE, accounts.new_session(user["name"], days),
                        max_age=days * 86400 if body.remember else None, httponly=True,
                        samesite="lax", secure=request.url.scheme == "https", path="/")
    activity.add(user["name"], "login")
    return response


@app.post("/api/logout")
def logout(request: Request):
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        accounts.end_session(token)
    response = Response(status_code=204)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


@app.get("/api/me")
def me(user=GUEST):
    return user


@app.post("/api/me/password", status_code=204)
def change_my_password(body: PasswordChange, request: Request, user=GUEST):
    if AUTH_DISABLED:
        raise ValueError("login desativado (AUTH_DISABLED=1)")
    accounts.change_password(user["name"], body.current, body.new)
    accounts.end_sessions(user["name"], keep=request.cookies.get(SESSION_COOKIE))
    activity.add(user["name"], "password", detail="própria senha")


@app.get("/api/me/tokens")
def my_tokens(user=GUEST):
    return accounts.tokens(user["name"])


@app.post("/api/me/tokens")
def create_token(body: TokenRequest, user=GUEST):
    return accounts.new_token(user["name"], body.label)


@app.delete("/api/me/tokens/{token_id}", status_code=204)
def revoke_token(token_id: str, user=GUEST):
    accounts.revoke_token(user["name"], token_id)


def get_prefs(user):
    """Per-user look: accent colour and the scenes hidden from the Cenas tab."""
    prefs = settings.data.get("prefs", {}) if AUTH_DISABLED else accounts.prefs(user["name"])
    return {"accent": prefs.get("accent"), "hidden_scenes": prefs.get("hidden_scenes", [])}


@app.get("/api/me/prefs")
def my_prefs(user=GUEST):
    return get_prefs(user)


@app.put("/api/me/prefs")
def set_my_prefs(body: Prefs, user=GUEST):
    prefs = {
        "accent": body.accent and "#%02x%02x%02x" % parse_color(body.accent),
        "hidden_scenes": [s[:32] for s in dict.fromkeys(body.hidden_scenes)][:200],
    }
    if AUTH_DISABLED:
        with settings.lock:
            settings.data["prefs"] = prefs
            settings.save()
    else:
        accounts.set_prefs(user["name"], prefs)
    return prefs


# --- users (admin)

@app.get("/api/users")
def list_users(user=ADMIN):
    return accounts.list_users()


@app.post("/api/users")
def create_user(body: NewUser, user=ADMIN):
    created = accounts.create(body.username, body.password, body.role)
    activity.add(user["name"], "user_add", detail=f"{created['name']} ({created['role']})")
    return created


@app.patch("/api/users/{name}")
def update_user(name: str, body: UserUpdate, user=ADMIN):
    updated = accounts.update(name, body.password, body.role)
    if body.password is not None:
        accounts.end_sessions(name)
        activity.add(user["name"], "password", detail=f"senha de {name}")
    if body.role is not None:
        activity.add(user["name"], "user_role", detail=f"{name} → {body.role}")
    return updated


@app.delete("/api/users/{name}", status_code=204)
def delete_user(name: str, user=ADMIN):
    if name == user["name"]:
        raise ValueError("você não pode excluir a si mesmo")
    accounts.delete(name)
    activity.add(user["name"], "user_delete", detail=name)


# --- info, status and live events

def full_status(target):
    devices = registry.resolve(target)
    status = registry.status(target)
    status["effect"] = effects.active(devices[0].id)
    status["circadian"] = all(effects.circadian_enabled(d.id) for d in devices)
    status["can_undo"] = any(d.history for d in devices)
    return status


@app.get("/api/info")
def get_info(user=GUEST):
    temperature, brightness = effects.circadian_point()
    return {
        "app": APP_NAME,
        "user": user,
        "prefs": get_prefs(user),
        "auth": not AUTH_DISABLED,
        "targets": registry.targets(),
        "default_target": registry.default_target,
        "scenes": library.scenes(),
        "favorites": library.favorites(),
        "location": sun.location,
        "sun": sun.today(),
        "circadian_now": {"temperature": temperature, "brightness": brightness},
        "presence": effects.presence(),
        "mqtt": {"enabled": bool(os.environ.get("MQTT_HOST")), "connected": bool(bridge and bridge.connected)},
    }


@app.get("/api/status")
def get_status(target: str | None = None, user=GUEST):
    return full_status(target)


@app.get("/api/events")
async def events(request: Request, user=GUEST):
    subscription = broker.subscribe()
    queue = subscription[1]

    async def stream():
        try:
            yield "retry: 3000\n\n"
            while True:
                try:
                    message = await asyncio.wait_for(queue.get(), 20)
                except TimeoutError:
                    yield ": ping\n\n"
                    continue
                yield f"data: {message}\n\n"
        finally:
            broker.unsubscribe(subscription)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# --- controls (?target= bulb id, group id or "all"; defaults to the first bulb)

def act(user, target, action, value=None, brightness=None):
    perform(user["name"], target, action, value, brightness)
    return full_status(target)


@app.post("/api/power")
def set_power(body: Power, target: str | None = None, user=GUEST):
    return act(user, target, "on" if body.on else "off")


@app.post("/api/toggle")
def toggle(target: str | None = None, user=GUEST):
    return act(user, target, "off" if registry.status(target)["on"] else "on")


@app.post("/api/color")
def set_color(body: Color, target: str | None = None, user=MEMBER):
    return act(user, target, "color", body.color, body.brightness)


@app.post("/api/brightness")
def set_brightness(body: Brightness, target: str | None = None, user=MEMBER):
    return act(user, target, "brightness", body.value)


@app.post("/api/white")
def set_white(body: White, target: str | None = None, user=MEMBER):
    return act(user, target, "white", body.temperature, body.brightness)


@app.post("/api/scene")
def set_scene(body: SceneChoice, target: str | None = None, user=MEMBER):
    return act(user, target, "scene", body.scene)


@app.post("/api/favorite")
def apply_favorite(body: FavoriteChoice, target: str | None = None, user=MEMBER):
    return act(user, target, "favorite", body.favorite)


@app.post("/api/timer")
def set_timer(body: Timer, target: str | None = None, user=MEMBER):
    return act(user, target, "timer", body.minutes)


@app.post("/api/undo")
def undo(target: str | None = None, user=MEMBER):
    devices = registry.resolve(target)
    effects.stop(devices)
    registry.undo(devices)
    activity.add(user["name"], "undo", registry.target_name(target))
    return full_status(target)


# --- routines

@app.post("/api/effects/fade")
def start_fade(body: Fade, target: str | None = None, user=MEMBER):
    return act(user, target, body.direction, body.minutes)


@app.post("/api/effects/pomodoro")
def start_pomodoro(body: Pomodoro, target: str | None = None, user=MEMBER):
    effects.pomodoro(registry.resolve(target), body.work, body.rest, body.cycles)
    activity.add(user["name"], "pomodoro", registry.target_name(target),
                 f"{body.cycles}× {body.work}/{body.rest} min")
    return full_status(target)


@app.post("/api/effects/flash")
def start_flash(body: Flash, target: str | None = None, user=MEMBER):
    colour = "#%02x%02x%02x" % parse_color(body.color)
    effects.flash(registry.resolve(target), colour, body.times)
    activity.add(user["name"], "flash", registry.target_name(target), colour)
    return {"ok": True}


@app.delete("/api/effects")
def stop_effects(target: str | None = None, user=MEMBER):
    effects.stop(registry.resolve(target), restore=True)
    return full_status(target)


@app.put("/api/circadian")
def set_circadian(body: Toggle, target: str | None = None, user=MEMBER):
    devices = registry.resolve(target)
    if body.enabled:
        effects.stop(devices)
    effects.set_circadian(devices, body.enabled)
    activity.add(user["name"], "circadian", registry.target_name(target), "ativado" if body.enabled else "desativado")
    return full_status(target)


@app.put("/api/presence")
def set_presence(body: Presence, user=MEMBER):
    effects.set_presence(body.enabled, body.target)
    activity.add(user["name"], "presence", registry.target_name(body.target),
                 "ativada" if body.enabled else "desativada")
    return effects.presence()


# --- scenes and favourites

@app.post("/api/scenes")
def create_scene(body: CustomScene, user=MEMBER):
    scene = library.save_scene(body.name, body.colors, body.transition, body.speed, body.brightness)
    activity.add(user["name"], "scene_add", detail=scene["name"])
    return scene


@app.post("/api/scenes/preview")
def preview_scene(body: CustomScene, target: str | None = None, user=MEMBER):
    """Try a scene from the editor without saving it."""
    if body.transition not in ("static", "jump", "fade"):
        raise ValueError("transição deve ser static, jump ou fade")
    data = custom_scene_data({**body.model_dump(), "number": 0x1f})
    devices = registry.resolve(target)
    effects.stop(devices)
    registry.apply(devices, lambda d: d.bulb.set_scene_data(data), remember=True)
    return full_status(target)


@app.delete("/api/scenes/{scene_id}", status_code=204)
def delete_scene(scene_id: str, user=MEMBER):
    library.delete_scene(scene_id)


@app.post("/api/favorites")
def create_favorite(body: Favorite, target: str | None = None, user=MEMBER):
    favorite = library.save_favorite(body.name, registry.status(target))
    activity.add(user["name"], "favorite_add", detail=favorite["name"])
    return favorite


@app.delete("/api/favorites/{favorite_id}", status_code=204)
def delete_favorite(favorite_id: str, user=MEMBER):
    library.delete_favorite(favorite_id)


# --- schedules

@app.get("/api/schedules")
def list_schedules(user=MEMBER):
    return schedules.list()


@app.post("/api/schedules")
def create_schedule(body: Schedule, user=MEMBER):
    return schedules.upsert(body.model_dump(), check_reference)


@app.put("/api/schedules/{schedule_id}")
def update_schedule(schedule_id: str, body: Schedule, user=MEMBER):
    return schedules.upsert({**body.model_dump(), "id": schedule_id}, check_reference)


@app.delete("/api/schedules/{schedule_id}", status_code=204)
def delete_schedule(schedule_id: str, user=MEMBER):
    if not schedules.delete(schedule_id):
        raise HTTPException(404, "schedule not found")


# --- history and usage

@app.get("/api/activity")
def list_activity(limit: int = 30, user=MEMBER):
    return activity.list(max(1, min(limit, 300)))


@app.get("/api/stats")
def usage_stats(target: str | None = None, days: int = 7, user=MEMBER):
    devices = registry.resolve(target)
    return {"days": registry.usage(devices, max(1, min(days, 90))), "watts": sum(d.watts for d in devices)}


# --- groups and settings (admin)

@app.post("/api/groups")
def create_group(body: Group, user=ADMIN):
    return registry.save_group(body.name, body.members)


@app.put("/api/groups/{group_id}")
def update_group(group_id: str, body: Group, user=ADMIN):
    return registry.save_group(body.name, body.members, group_id)


@app.delete("/api/groups/{group_id}", status_code=204)
def delete_group(group_id: str, user=ADMIN):
    registry.delete_group(group_id)


@app.put("/api/settings/location")
def set_location(body: Location, user=ADMIN):
    with settings.lock:
        has_location = body.lat is not None and body.lon is not None
        settings.data["location"] = {"lat": body.lat, "lon": body.lon} if has_location else None
        settings.save()
    schedules.refresh_sun()
    broker.publish("settings")
    return {"location": sun.location, "sun": sun.today()}


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
