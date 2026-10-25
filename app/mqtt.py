"""Optional MQTT bridge with Home Assistant discovery (enabled when MQTT_HOST is set).

Each bulb shows up in Home Assistant as a JSON-schema light, which also makes it
available to Alexa / Google Assistant through Home Assistant.
"""
import json
import logging
import secrets

log = logging.getLogger(__name__)

KELVIN_MIN, KELVIN_MAX = 2700, 6500


def to_kelvin(temperature):
    return round(KELVIN_MIN + (KELVIN_MAX - KELVIN_MIN) * temperature / 100)


def from_kelvin(kelvin):
    return max(0, min(100, round((kelvin - KELVIN_MIN) * 100 / (KELVIN_MAX - KELVIN_MIN))))


class MqttBridge:
    def __init__(self, config, registry, library, command):
        """`command(device_id, action, value, **extra)` performs an action as the "mqtt" actor."""
        import paho.mqtt.client as mqtt

        self._registry = registry
        self._library = library
        self._command = command
        self._prefix = config.get("prefix") or "smartbulb"
        self._discovery = config.get("discovery_prefix") or "homeassistant"
        self._host = config["host"]
        self._port = int(config.get("port") or 1883)
        self._client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"smartbulb-{secrets.token_hex(3)}")
        if config.get("user"):
            self._client.username_pw_set(config["user"], config.get("password"))
        self._client.will_set(self._availability, "offline", retain=True)
        self._client.on_connect = self._on_connect
        self._client.on_message = self._on_message
        self.connected = False
        registry.listeners.append(self.publish_state)

    @property
    def _availability(self):
        return f"{self._prefix}/availability"

    def start(self):
        self._client.connect_async(self._host, self._port)
        self._client.loop_start()

    def stop(self):
        self._client.publish(self._availability, "offline", retain=True)
        self._client.disconnect()
        self._client.loop_stop()

    def _on_connect(self, client, userdata, flags, reason, properties):
        self.connected = not reason.is_failure
        if reason.is_failure:
            log.warning("MQTT connection failed: %s", reason)
            return
        log.info("MQTT connected to %s:%s", self._host, self._port)
        client.publish(self._availability, "online", retain=True)
        effects = [s["label"] for s in self._library.scenes()]
        for device in self._registry.devices.values():
            base = f"{self._prefix}/{device.id}"
            config = {
                "name": None,
                "unique_id": f"smartbulb_{device.id}",
                "schema": "json",
                "command_topic": f"{base}/set",
                "state_topic": f"{base}/state",
                "availability_topic": self._availability,
                "brightness": True,
                "brightness_scale": 100,
                "supported_color_modes": ["color_temp", "rgb"],
                "color_temp_kelvin": True,
                "min_kelvin": KELVIN_MIN,
                "max_kelvin": KELVIN_MAX,
                "effect": True,
                "effect_list": effects,
                "device": {"identifiers": [f"smartbulb_{device.id}"], "name": device.name,
                           "manufacturer": "Tuya", "model": "SmartBulb"},
            }
            client.publish(f"{self._discovery}/light/smartbulb_{device.id}/config", json.dumps(config), retain=True)
            client.subscribe(f"{base}/set")
            self.publish_state(device, self._registry.state(device))

    def publish_state(self, device, state):
        if not self.connected or state is None:
            return
        payload = {"state": "ON" if state["on"] else "OFF", "brightness": state["brightness"], "effect": None}
        if state["mode"] == "colour" and state["color"]:
            c = state["color"]
            payload.update(color_mode="rgb", color={"r": int(c[1:3], 16), "g": int(c[3:5], 16), "b": int(c[5:7], 16)})
        else:
            payload.update(color_mode="color_temp", color_temp=to_kelvin(state["temperature"]))
        if state["mode"] == "scene" and state.get("scene"):
            payload["effect"] = self._library.scene_label(state["scene"])
        self._client.publish(f"{self._prefix}/{device.id}/state", json.dumps(payload), retain=True)

    def _on_message(self, client, userdata, message):
        device_id = message.topic.split("/")[-2]
        try:
            data = json.loads(message.payload)
            if device_id not in self._registry.devices:
                return
            if data.get("state") == "OFF":
                self._command(device_id, "off")
            elif data.get("effect"):
                scene = next((s["id"] for s in self._library.scenes() if s["label"] == data["effect"]), None)
                if scene:
                    self._command(device_id, "scene", scene)
            elif data.get("color"):
                c = data["color"]
                self._command(device_id, "color", "#%02x%02x%02x" % (c["r"], c["g"], c["b"]),
                              brightness=data.get("brightness"))
            elif data.get("color_temp"):
                self._command(device_id, "white", from_kelvin(data["color_temp"]), brightness=data.get("brightness"))
            elif data.get("brightness"):
                self._command(device_id, "brightness", max(1, int(data["brightness"])))
            else:
                self._command(device_id, "on")
        except Exception:
            log.exception("MQTT command failed: %s", message.payload[:200])
