# SmartBulb

**English** · [Português (BR)](README.pt-BR.md)

Self-hosted, cloud-free control for Tuya-based Wi-Fi bulbs (tested with the Elgin "Smart lamp",
protocol 3.5). No proprietary app: a responsive web UI (desktop and mobile), scenes, routines,
schedules, multiple users, a REST API, a CLI and Home Assistant integration, all talking to the bulb
directly over your LAN.

```
smartbulb/
├── app/
│   ├── bulb.py          # bulb control (tinytuya, local protocol) and scenes
│   ├── devices.py       # multiple bulbs, groups, status cache, undo and usage stats
│   ├── effects.py       # routines: fades, pomodoro, alert flash, circadian rhythm, presence
│   ├── schedules.py     # schedules (fixed time or sunrise/sunset)
│   ├── library.py       # favourites and user-made scenes
│   ├── auth.py          # users, sessions and API tokens
│   ├── activity.py      # who changed what
│   ├── sun.py           # sunrise and sunset from latitude/longitude
│   ├── mqtt.py          # MQTT bridge with Home Assistant discovery
│   ├── events.py        # live updates (Server-Sent Events)
│   ├── main.py          # FastAPI API
│   ├── cli.py           # command-line client
│   └── static/          # web UI and login page
├── Containerfile
└── deploy/smartbulb.container   # Podman Quadlet unit (systemd)
```

## Features

**Control**
- Power, white temperature and brightness
- Colour from presets or a built-in picker: saturation/value square, hue bar, HEX and RGB fields
- 38 scenes in three groups: **Work** (deep focus, study, reading, video call, creativity, evening
  work…), **Relax** (sunset, dusk, candle, fireplace, ocean, rain, forest, aurora, meditation,
  romantic, spa, moonlight, cinema…) and **Fun**
- Make your own scenes (up to 8 colours; static, jump or fade; speed and brightness) and try them
  on the bulb before saving
- Favourites: save the current light and bring it back with one tap
- Undo the last change
- Feedback on every button: loading spinner, confirmation and error message

**Routines**
- **Circadian rhythm**: tone and brightness follow the day (cool and bright in the morning, warm and
  dim at night), based on sunrise and sunset
- **Wake up / Fall asleep**: gradual sunrise, or dim until off, over 5 to 60 minutes
- **Pomodoro**: cool focus light and a soft colour during breaks, with a blink between phases
- **Presence simulation**: lights on and off at random from sunset until 23:30
- **Alert** (`/api/effects/flash`): blinks a colour and goes back to the previous light (doorbell,
  failed build…)
- Auto-off timer

**Schedules**
- Per weekday, at a fixed time or relative to sunrise/sunset (e.g. "20 min before sunset")
- Actions: on, off, colour, white, brightness, scene, favourite, wake up, fall asleep, circadian

**Home and users**
- A proper login page with cookie sessions
- Multiple users with roles: **admin** (everything, including users and groups), **user** (all
  light controls) and **guest** (on/off only)
- Admins add users, change roles and reset passwords; everyone can change their own password
- Per-user API tokens for the CLI, scripts, iPhone Shortcuts and Home Assistant
- Activity history (who changed what, and when)
- Multiple bulbs and groups (e.g. "Living room" with two bulbs)
- Usage stats: hours on per day and an estimated kWh
- Live updates: when someone changes the light, every open screen shows it right away
- MQTT with Home Assistant discovery (and, through it, Alexa and Google Assistant)
- Light and dark themes; can be installed on the phone home screen
- Secrets via Podman secrets, rootless and read-only container

## Requirements

- Any Tuya bulb that exposes the standard light DPS (20–26). Other Tuya bulbs will likely work, but
  only the bulb above has been tested.
- A server **on the same LAN** as the bulb (it connects straight to `<bulb-ip>:6668`).
- The bulb's IP reserved in your router's DHCP, and its `local_key`.

## Getting the local key

1. Pair the bulb with the **Smart Life** app. Brand apps such as Elgin Smart can't be linked to the
   Tuya developer platform.
2. On [platform.tuya.com](https://platform.tuya.com): **Cloud → Development → Create Cloud Project**
   (Smart Home, pick your data center), then **Devices → Link App Account** and scan the QR code
   with Smart Life.
3. Run the wizard with the project's Access ID/Secret:
   ```bash
   pip install tinytuya
   ```
   ```bash
   python -m tinytuya wizard
   ```
   It writes `devices.json` with the device `id`, `ip` and `key`.
4. Optional but recommended: block the bulb's internet access on your router. Local control keeps
   working. Don't re-pair it afterwards, because pairing generates a new key.

## Trying it without a bulb

Demo mode simulates bulbs in memory:

```bash
pip install -r requirements.txt
```

```bash
BULB_DEMO=2 DATA_DIR=data APP_PASSWORD=change-this-password uvicorn app.main:app --port 8080
```

## Deployment (rootless Podman + Quadlet)

On the server, in the project folder:

```bash
podman build -t localhost/smartbulb:latest .
```

Create the secrets (they never live in files or in the container environment). The key comes from
the wizard's `devices.json`:

```bash
python3 -c "import json;print(json.load(open('devices.json'))[0]['key'],end='')" | podman secret create bulb_key -
```

```bash
read -rsp 'First admin password: ' PW && printf %s "$PW" | podman secret create app_password - && unset PW
```

Configuration and service:

```bash
mkdir -p ~/.config/smartbulb ~/.config/containers/systemd
cp .env.example ~/.config/smartbulb/smartbulb.env      # edit BULB_ID / BULB_IP / LATITUDE / LONGITUDE
cp deploy/smartbulb.container ~/.config/containers/systemd/
systemctl --user daemon-reload
systemctl --user start smartbulb
loginctl enable-linger $USER                           # keep running without a login session
```

Open `http://<server-ip>:8080` and sign in with `admin` and the password you created. On Fedora,
open the port on the LAN if needed:

```bash
sudo firewall-cmd --permanent --add-port=8080/tcp && sudo firewall-cmd --reload
```

Logs and updates:

```bash
journalctl --user -u smartbulb -f
```

```bash
podman build -t localhost/smartbulb:latest . && systemctl --user restart smartbulb
```

Users, schedules, favourites, groups, history and stats live in the `smartbulb-data` volume and
survive restarts and updates.

### Users

`APP_USER`/`APP_PASSWORD` only create the **first** admin, when no user exists yet. After that,
users live in `/data/users.json` (scrypt-hashed passwords) and are managed under **Administração**
in the avatar menu. Changing the `app_password` secret later doesn't change anyone's password.

### Multiple bulbs

Point `BULBS_FILE` at a JSON file in the wizard's `devices.json` format, listing only the bulbs (it
holds the keys, so mount it as a secret):

```json
[
  {"id": "...", "name": "Living room", "ip": "192.168.1.20", "key": "...", "version": "3.5", "watts": 9},
  {"id": "...", "name": "Bedroom", "ip": "192.168.1.21", "key": "...", "version": "3.5"}
]
```

```bash
podman secret create bulbs ./bulbs.json
```

Uncomment the `BULBS_FILE` lines in `deploy/smartbulb.container`. Groups are created in the UI under
**Administração → Grupos**.

### Home Assistant (MQTT)

Set `MQTT_HOST` (and `MQTT_USER`/`MQTT_PASSWORD` if your broker needs them). Each bulb shows up in
Home Assistant on its own as a light with brightness, colour temperature, colour and the scenes as
effects. From there, Alexa and Google Assistant work through Home Assistant's integrations.

## Remote access

**Don't expose port 8080 directly to the internet.** Plain HTTP sends the password in clear text.
From simplest to most work:

1. **Tailscale** (recommended): install it on the server and your phone, then open
   `http://<server-name>:8080` from anywhere with no router ports opened. `tailscale serve --bg 8080`
   adds HTTPS at `https://<server>.<tailnet>.ts.net`.
2. **WireGuard** on your router or server: the same idea, fully self-hosted.
3. **HTTPS reverse proxy** (Caddy, Traefik, Nginx) or **Cloudflare Tunnel** for a public domain.
   Use strong passwords and only serve the UI over HTTPS.

Behind HTTPS the session cookie is marked `Secure` automatically. "Usar minha localização" in the
admin dialog also needs HTTPS.

## API

Every route except `/healthz` and `/api/login` requires authentication: the web UI's session
cookie, an API token (`Authorization: Bearer sb_...`, created under **Minha conta**) or HTTP Basic
with a username and password.

Control routes take `?target=` with a bulb id, a group id or `all`. Without it, the first bulb is
used.

| Method | Route | Body |
|---|---|---|
| GET | `/api/info` | none (bulbs, groups, scenes, favourites, sun times) |
| GET | `/api/status` | none |
| GET | `/api/events` | none (live event stream) |
| POST | `/api/power` | `{"on": true}` |
| POST | `/api/toggle` | none |
| POST | `/api/color` | `{"color": "#ff8800", "brightness": 60}` (`brightness` is optional) |
| POST | `/api/brightness` | `{"value": 1-100}` |
| POST | `/api/white` | `{"temperature": 0-100, "brightness": 1-100}` (0 = warm; both optional) |
| POST | `/api/scene` | `{"scene": "focus"}` |
| POST | `/api/favorite` | `{"favorite": "<id>"}` |
| POST | `/api/timer` | `{"minutes": 30}` (0 cancels) |
| POST | `/api/undo` | none |
| POST | `/api/effects/fade` | `{"direction": "wake" or "sleep", "minutes": 15}` |
| POST | `/api/effects/pomodoro` | `{"work": 25, "rest": 5, "cycles": 4}` |
| POST | `/api/effects/flash` | `{"color": "#ff0000", "times": 3}` |
| DELETE | `/api/effects` | none (stops the routine and restores the light) |
| PUT | `/api/circadian` | `{"enabled": true}` |
| PUT | `/api/presence` | `{"enabled": true, "target": "<id>"}` |
| POST | `/api/favorites` | `{"name": "Reading"}` (saves the current light) |
| POST | `/api/scenes` | `{"name": "...", "colors": ["#ff0000"], "transition": "fade", "speed": 50, "brightness": 80}` |
| GET/POST | `/api/schedules` | `{"trigger": "sunset", "offset": -20, "days": [0,1,2,3,4], "action": "on", "target": null}` |
| PUT/DELETE | `/api/schedules/{id}` | same as POST / none |
| GET | `/api/activity`, `/api/stats` | none |
| GET/POST/PATCH/DELETE | `/api/users`, `/api/groups` | admins only |

`trigger`: `time` (uses `time`, "HH:MM"), `sunrise` or `sunset` (uses `offset` in minutes). `days`:
0 = Monday … 6 = Sunday. `action`: `on`, `off`, `color`, `white`, `brightness`, `scene`, `favorite`,
`wake`, `sleep`, `circadian` (with `value` where it applies).

Examples:

```bash
curl -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d '{"on":false}' http://server:8080/api/power
```

```bash
curl -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d '{"color":"#ff0000"}' http://server:8080/api/effects/flash
```

## CLI

With `SMARTBULB_URL` and `SMARTBULB_TOKEN` set, the CLI goes through the server, so routines,
history and live updates work. Without them it talks to the bulb directly (reads `~/devices.json`
or `BULB_ID`/`BULB_IP`/`BULB_KEY`).

```bash
pip install -r requirements.txt
```

```bash
export SMARTBULB_URL=http://server:8080 SMARTBULB_TOKEN=sb_...
```

```bash
python3 -m app.cli --target living-room-id scene ocean
```

Commands: `status`, `on`, `off`, `toggle`, `color <name|#rrggbb>`, `brightness <1-100>`,
`white [0-100]`, `scene <name>`, `scenes`, `timer <minutes>`, and through the server only
`favorite <id>`, `undo`, `flash [#colour] [times]`, `wake [min]`, `sleep [min]`,
`pomodoro [work] [rest] [cycles]` and `stop`.

Inside the container:

```bash
podman exec smartbulb python -m app.cli scene night
```

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `BULB_ID`, `BULB_IP`, `BULB_VERSION` | none / `Auto` / `3.5` | bulb details |
| `BULB_KEY` or `BULB_KEY_FILE` | none | local key (prefer the file/secret) |
| `BULB_LABEL` | `Smart lamp` | bulb name shown in the UI |
| `BULB_WATTS` | `9` | power draw, for the usage estimate |
| `BULB_DEVICES_JSON`, `BULB_NAME` | `~/devices.json`, `Smart color` | alternative to the variables above |
| `BULBS_FILE` | none | JSON with several bulbs (replaces the variables above) |
| `BULB_DEMO` | unset | `N` simulates N bulbs, for trying the app without hardware |
| `SCENE_SPEED_INVERT` | unset | `1` flips the speed of animated scenes (see Notes) |
| `APP_USER` | `admin` | name of the first admin |
| `APP_PASSWORD` or `APP_PASSWORD_FILE` | none | password of the first admin |
| `AUTH_DISABLED` | unset | `1` disables login (trusted networks only) |
| `LATITUDE`, `LONGITUDE` | none | location for sunrise/sunset (also editable in the UI) |
| `TZ` | `America/Sao_Paulo` | timezone |
| `POLL_SECONDS` | `5` | how often bulb state is read |
| `MQTT_HOST`, `MQTT_PORT` | none, `1883` | MQTT broker (enables the integration) |
| `MQTT_USER`, `MQTT_PASSWORD` or `MQTT_PASSWORD_FILE` | none | broker credentials |
| `MQTT_PREFIX`, `MQTT_DISCOVERY_PREFIX` | `smartbulb`, `homeassistant` | topics |
| `DATA_DIR` | `/data` | where data is stored |

## Notes

- If the bulb is reset or re-paired, its `local_key` changes, and sometimes its ID too. Run the
  wizard again and recreate the `bulb_key` secret.
- Each bulb accepts only one connection at a time, so commands to the same bulb run one after
  another.
- Animated scenes step through 8 in-between shades so colour changes are smooth. Tuya doesn't
  document whether a larger speed value is faster or slower; if the "slow" scenes (fireplace, ocean)
  run faster than the "fast" ones (candle, party), set `SCENE_SPEED_INVERT=1` and restart.
- The web UI text is in Brazilian Portuguese.

## License

[MIT](LICENSE)
