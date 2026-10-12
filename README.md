# anova-nano-mqtt-broker

Bridges an **Anova Nano** sous vide cooker (Bluetooth LE) to MQTT, with Home Assistant auto-discovery.

All device communication goes through [pyanova-nano](https://github.com/filmkorn/pyanova-nano). This service only maps its API onto MQTT topics.

```
Anova Nano ⇄ BLE ⇄ host BlueZ ⇄ D-Bus ⇄ bridge (pyanova-nano) ⇄ MQTT ⇄ Home Assistant / Node-RED / …
```

## Quick start

```bash
docker compose up -d
docker compose logs -f anova-nano-bridge
```

The Nano accepts only one BLE connection. Close the Anova app (or turn off Bluetooth on your phone) before starting the bridge.

On first start the bridge scans for the cooker. Once you know its address (it's in the logs), set `DEVICE_ADDRESS` so the bridge connects directly.

## MQTT topics

With the default prefix `anova/nano`:

| Topic | Direction | Payload |
|---|---|---|
| `anova/nano/availability` | out, retained | `online` / `offline` (also the MQTT last will) |
| `anova/nano/state` | out, retained | JSON, see below |
| `anova/nano/info` | out, retained | JSON: `address`, `firmware_version`, `firmware_commit`, `firmware_date`, `device_info` |
| `anova/nano/set/power` | in | `ON` / `OFF` (also `start`/`stop`, `true`/`false`, `1`/`0`) |
| `anova/nano/set/mode` | in | `heat` / `off` (Home Assistant climate) |
| `anova/nano/set/target_temperature` | in | number, **in the cooker's current unit** (like `PyAnova.set_target_temperature`) |
| `anova/nano/set/timer` | in | whole minutes, 0–5999 |
| `anova/nano/set/unit` | in | `C` / `F` |

Example `state` payload:

```json
{
  "water_temp": 58.23, "water_temp_units": "C",
  "heater_temp": 61.1, "heater_temp_units": "C",
  "triac_temp": 45.12, "triac_temp_units": "C",
  "internal_temp": 35.05, "internal_temp_units": "C",
  "water_low": false, "water_leak": false, "motor_speed": 1800,
  "target_temperature": 60.0, "unit": "C", "timer": 90,
  "running": true, "status": "running"
}
```

This contains every field of pyanova-nano's `SensorValues`, plus the results of `get_target_temperature()`, `get_unit()`, `get_timer()` and `get_status()`.

Command handling:

- Invalid commands (out-of-range temperature, bad payload) are rejected and logged, never sent to the cooker.
- After each accepted command the state is refreshed immediately.
- Commands that arrive while the cooker is offline are **dropped, not replayed**. A `start` should not fire hours later when the cooker comes back in range.

## Home Assistant

With `HA_DISCOVERY=true` (the default) the bridge publishes discovery configs for one device. That device has:

- a climate entity (heat/off, target, current temperature)
- a "Cooking" switch
- water, heater, triac and internal temperature sensors, plus motor speed and timer remaining
- water low and water leak binary sensors
- a cooking timer number
- a display unit select

Discovery is re-sent when Home Assistant restarts (`homeassistant/status`) and whenever the cooker's unit changes.

## Configuration

All configuration is via environment variables:

| Variable | Default | Description |
|---|---|---|
| `MQTT_BROKER` | `mqtt-broker` | Broker host |
| `MQTT_PORT` | `1883` | Broker port |
| `MQTT_USER` / `MQTT_PASSWORD` | unset | Credentials |
| `MQTT_CLIENT_ID` | `anova-nano-bridge` | MQTT client id |
| `MQTT_TOPIC_PREFIX` | `anova/nano` | Topic prefix |
| `DEVICE_ADDRESS` | unset | BLE MAC of the cooker. If unset, the first Nano found is used |
| `SCAN_TIMEOUT` | `10` | Seconds to scan / look for the device |
| `POLL_INTERVAL` | `30` | Seconds between state polls (PyAnova's default) |
| `RECONNECT_MIN` / `RECONNECT_MAX` | `5` / `300` | BLE reconnect backoff bounds, seconds |
| `HA_DISCOVERY` | `true` | Publish Home Assistant discovery |
| `HA_DISCOVERY_PREFIX` | `homeassistant` | Discovery prefix |
| `DEVICE_NAME` | `Anova Nano` | Device name in Home Assistant |
| `LOG_LEVEL` | `INFO` | `DEBUG` shows every state poll |

## Bluetooth access

bleak uses the **host's** BlueZ over the system D-Bus. The container needs two things:

1. The host's D-Bus socket. `docker-compose.yml` mounts `/var/run/dbus`.
2. Permission to talk to `org.bluez`. BlueZ's default D-Bus policy allows root and members of the host's `bluetooth` group, so the image runs as root. No `privileged` mode or device passthrough is needed.

To run unprivileged instead, run the container as a host user that is in the `bluetooth` group. D-Bus checks group membership of the host uid. For example:

```yaml
    user: "1000:1000"   # a host user that is in the bluetooth group
```

The host needs a working Bluetooth adapter with `bluetoothd` running (`systemctl status bluetooth`).

## Running without Docker

```bash
pip install .
anova-nano-mqtt-broker          # or: python anova_mqtt_service.py
```

## Development

```bash
pip install -r requirements.txt pytest ruff mypy
ruff check . && ruff format --check .
mypy anova_mqtt_service.py
pytest -v
```

The tests run the bridge end to end against fake MQTT and BLE clients. They also check that every `PyAnova` method the bridge calls exists on the real class, so an upstream API change fails CI.
