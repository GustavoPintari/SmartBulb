# SmartBulb

**English** · [Português (BR)](README.pt-BR.md)

Self-hosted, cloud-free control for a Tuya-based Wi-Fi bulb (tested with the Elgin "Smart lamp",
protocol 3.5). No proprietary app: a responsive web UI (desktop and mobile), built-in scenes,
schedules, a REST API and a CLI, all talking to the bulb directly over your LAN.

```
smartbulb/
├── app/
│   ├── bulb.py          # bulb control (tinytuya, local protocol)
│   ├── schedules.py     # schedules persisted to /data/schedules.json
│   ├── main.py          # FastAPI API + authentication
│   ├── cli.py           # command-line client
│   └── static/          # web UI
├── Containerfile
└── deploy/smartbulb.container   # Podman Quadlet unit (systemd)
```

## Features

- Power, colour (presets and colour picker), white temperature and brightness
- The 8 built-in Tuya scenes: night, read, work, leisure, soft, rainbow, shine, gorgeous
- Auto-off timer
- Recurring schedules per weekday, run inside the container (no cron needed)
- Light and dark themes; can be installed on the phone home screen
- HTTP Basic login, secrets via Podman secrets, rootless and read-only container

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
read -rsp 'Web UI password: ' PW && printf %s "$PW" | podman secret create app_password - && unset PW
```

Configuration and service:

```bash
mkdir -p ~/.config/smartbulb ~/.config/containers/systemd
cp .env.example ~/.config/smartbulb/smartbulb.env      # edit BULB_ID / BULB_IP / APP_USER
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

Schedules live in the `smartbulb-data` volume and survive restarts and updates.

## Remote access

**Don't expose port 8080 directly to the internet.** Plain HTTP sends the password in clear text.
From simplest to most work:

1. **Tailscale** (recommended): install it on the server and your phone, then open
   `http://<server-name>:8080` from anywhere with no router ports opened. `tailscale serve --bg 8080`
   adds HTTPS at `https://<server>.<tailnet>.ts.net`.
2. **WireGuard** on your router or server: the same idea, fully self-hosted.
3. **HTTPS reverse proxy** (Caddy, Traefik, Nginx) or **Cloudflare Tunnel** for a public domain.
   Use a strong password. The login is HTTP Basic, so only serve it over HTTPS.

## API

Every route except `/healthz` requires HTTP Basic auth.

| Method | Route | Body |
|---|---|---|
| GET | `/api/info` | none (name and scene list) |
| GET | `/api/status` | none |
| POST | `/api/power` | `{"on": true}` |
| POST | `/api/toggle` | none |
| POST | `/api/color` | `{"color": "#ff8800"}` or `{"color": "red"}` |
| POST | `/api/brightness` | `{"value": 1-100}` |
| POST | `/api/white` | `{"temperature": 0-100}` (0 = warm) |
| POST | `/api/scene` | `{"scene": "night"}` |
| POST | `/api/timer` | `{"minutes": 30}` (0 cancels) |
| GET | `/api/schedules` | none |
| POST | `/api/schedules` | `{"time": "18:30", "days": [0,1,2,3,4], "action": "on"}` |
| PUT | `/api/schedules/{id}` | same as POST |
| DELETE | `/api/schedules/{id}` | none |

`days`: 0 = Monday … 6 = Sunday. `action`: `on`, `off`, `color`, `white`, `brightness`, `scene`
(with `value` where it applies).

Scenes: `night`, `read`, `work`, `leisure`, `soft`, `rainbow`, `shine`, `gorgeous`. These are
Tuya's factory scenes, written to DPS 25.

Example:

```bash
curl -u admin:PASSWORD -H 'Content-Type: application/json' -d '{"on":false}' http://server:8080/api/power
```

## CLI

Outside the container, from the project folder. It reads `~/devices.json` or
`BULB_ID`/`BULB_IP`/`BULB_KEY`:

```bash
pip install -r requirements.txt
```

```bash
python3 -m app.cli status
```

Commands: `status`, `on`, `off`, `toggle`, `color <name|#rrggbb>`, `brightness <1-100>`,
`white [0-100]`, `scene <name>`, `timer <minutes>`.

Inside the container:

```bash
podman exec smartbulb python -m app.cli scene night
```

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `BULB_ID`, `BULB_IP`, `BULB_VERSION` | none / `Auto` / `3.5` | bulb details |
| `BULB_KEY` or `BULB_KEY_FILE` | none | local key (prefer the file/secret) |
| `BULB_LABEL` | `Smart lamp` | name shown in the UI |
| `BULB_DEVICES_JSON`, `BULB_NAME` | `~/devices.json`, `Smart color` | alternative to the variables above |
| `APP_USER` | `admin` | UI username |
| `APP_PASSWORD` or `APP_PASSWORD_FILE` | none | password (required) |
| `AUTH_DISABLED` | unset | `1` disables login (trusted networks only) |
| `TZ` | `America/Sao_Paulo` | timezone for schedules |
| `DATA_DIR` | `/data` | where `schedules.json` is stored |

## Notes

- If the bulb is reset or re-paired, its `local_key` changes, and sometimes its ID too. Run the
  wizard again and recreate the `bulb_key` secret.
- The bulb accepts only one connection at a time, so the API serialises commands.
- The web UI text is in Brazilian Portuguese.

## License

[MIT](LICENSE)
