import base64
import logging
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .bulb import SCENES, Bulb, BulbConfig, BulbError
from .schedules import ScheduleStore

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))

STATIC_DIR = Path(__file__).parent / "static"
DATA_DIR = os.environ.get("DATA_DIR", "/data")


def read_secret(name):
    """Read NAME from the environment, or from the file at NAME_FILE (podman secrets)."""
    if os.environ.get(name):
        return os.environ[name]
    path = os.environ.get(f"{name}_FILE")
    if path:
        with open(path) as f:
            return f.read().strip()
    return None


BULB_LABEL = os.environ.get("BULB_LABEL", "Smart lamp")
APP_USER = os.environ.get("APP_USER", "admin")
APP_PASSWORD = read_secret("APP_PASSWORD")
AUTH_DISABLED = os.environ.get("AUTH_DISABLED") == "1"
if not APP_PASSWORD and not AUTH_DISABLED:
    raise SystemExit("Set APP_PASSWORD (or APP_PASSWORD_FILE), or AUTH_DISABLED=1 for a trusted network")

bulb = Bulb(BulbConfig.load())
schedules = ScheduleStore(bulb, os.path.join(DATA_DIR, "schedules.json"), os.environ.get("TZ"))


@asynccontextmanager
async def lifespan(app):
    schedules.start()
    yield
    schedules.shutdown()


app = FastAPI(title="smartbulb", lifespan=lifespan, docs_url=None, redoc_url=None)


def is_authorized(header):
    if not header or not header.startswith("Basic "):
        return False
    try:
        user, _, password = base64.b64decode(header[6:]).decode().partition(":")
    except Exception:
        return False
    return secrets.compare_digest(user, APP_USER) & secrets.compare_digest(password, APP_PASSWORD)


@app.middleware("http")
async def basic_auth(request: Request, call_next):
    if AUTH_DISABLED or request.url.path == "/healthz":
        return await call_next(request)
    if not is_authorized(request.headers.get("authorization")):
        return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="smartbulb"'})
    return await call_next(request)


@app.exception_handler(BulbError)
async def bulb_error(request, exc):
    return JSONResponse(status_code=502, content={"detail": f"bulb unreachable: {exc}"})


@app.exception_handler(ValueError)
async def value_error(request, exc):
    return JSONResponse(status_code=422, content={"detail": str(exc)})


class Power(BaseModel):
    on: bool


class Color(BaseModel):
    color: str


class Brightness(BaseModel):
    value: int = Field(ge=1, le=100)


class White(BaseModel):
    temperature: int | None = Field(default=None, ge=0, le=100)


class Scene(BaseModel):
    scene: str


class Timer(BaseModel):
    minutes: float = Field(ge=0, le=24 * 60)


class Schedule(BaseModel):
    time: str
    days: list[int] = list(range(7))
    action: str
    value: str | int | None = None
    enabled: bool = True


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/info")
def get_info():
    return {
        "name": BULB_LABEL,
        "scenes": [{"id": k, "label": label} for k, (label, _) in SCENES.items()],
    }


@app.get("/api/status")
def get_status():
    return bulb.status()


@app.post("/api/power")
def set_power(body: Power):
    bulb.turn_on() if body.on else bulb.turn_off()
    return bulb.status()


@app.post("/api/toggle")
def toggle():
    bulb.toggle()
    return bulb.status()


@app.post("/api/color")
def set_color(body: Color):
    bulb.set_color(body.color)
    return bulb.status()


@app.post("/api/brightness")
def set_brightness(body: Brightness):
    bulb.set_brightness(body.value)
    return bulb.status()


@app.post("/api/white")
def set_white(body: White):
    bulb.set_white(body.temperature)
    return bulb.status()


@app.post("/api/scene")
def set_scene(body: Scene):
    bulb.set_scene(body.scene)
    return bulb.status()


@app.post("/api/timer")
def set_timer(body: Timer):
    bulb.set_timer(body.minutes)
    return bulb.status()


@app.get("/api/schedules")
def list_schedules():
    return schedules.list()


@app.post("/api/schedules")
def create_schedule(body: Schedule):
    return schedules.upsert(body.model_dump())


@app.put("/api/schedules/{schedule_id}")
def update_schedule(schedule_id: str, body: Schedule):
    return schedules.upsert({**body.model_dump(), "id": schedule_id})


@app.delete("/api/schedules/{schedule_id}", status_code=204)
def delete_schedule(schedule_id: str):
    if not schedules.delete(schedule_id):
        raise HTTPException(404, "schedule not found")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
