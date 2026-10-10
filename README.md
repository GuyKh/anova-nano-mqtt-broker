# anova-nano-to-mqtt

Docker service that sends Anova Nano BLE events to MQTT.

## Overview

This project bridges the Anova Nano sous vide cooker (via Bluetooth LE) with an MQTT broker,
allowing you to receive Anova Nano events (temperature, power status, alerts) in any MQTT
compatible system (Home Assistant, Node-RED, etc.).

## Architecture

```
Anova Nano (BLE) → Python Service → MQTT Broker → Consumers
```

The Python service uses:
- `pyanova-nano` for BLE communication with the Anova Nano
- `paho-mqtt` for MQTT publishing
- `blueZ` system libraries for Bluetooth access (included in Dockerfile)

## MQTT Topics

| Topic | Description |
|-------|-------------|
| `anova/nano/status` | General device status |
| `anova/nano/temperature` | Current cooking temperature |
| `anova/nano/power` | Power state and cooking status |
| `anova/nano/alert` | Alerts and warnings |

## Project Structure

```
anova-nano-to-mqtt/
├── Dockerfile              # Python 3.12-slim with BlueZ
├── requirements.txt        # pyanova-nano + paho-mqtt
├── anova_mqtt_service.py   # Main bridge script
├── docker-compose.yml      # Mosquitto + bridge services
├── mosquitto/config/       # MQTT broker configuration
└── README.md              # This file
```

## Usage

### Start the services

```bash
cd anova-nano-to-mqtt
docker compose up -d
```

### View logs

```bash
docker compose logs -f anova-nano-bridge
```

### First run

On first run, the service will scan for your Anova Nano device and connect via BLE.
Ensure your Anova Nano is in pairing mode and within Bluetooth range.

## Configuration

Edit `mosquitto/config/mosquitto.conf` to adjust MQTT settings (port, password, etc.).

## Bluetooth Access

The Dockerfile includes BlueZ for direct Bluetooth access. The docker-compose.yml is
configured to automatically add the host's Bluetooth group to the container:

```bash
docker compose up -d
```

If your system doesn't support `group_add` in compose, you can use:

```bash
# Alternative: Manual group addition
docker compose run --rm --group-add bluetooth anova-nano-bridge

# Or modify docker-compose.yml to add:
# group_add:
#   - bluetooth
```

**Note**: Ensure your user is in the `bluetooth` group on the host system for proper
device access, or run with appropriate privileges.

## Alternative: ESP32 BLE→MQTT Proxy

If you prefer not to give Docker access to Bluetooth hardware, use an ESP32 running
[Tasmota/ESPHome BLE proxy] to forward BLE advertisements to MQTT without BlueZ.

## Development

### Running Locally (without Docker)

```bash
# Install dependencies
pip install -r requirements.txt

# Run the service
python anova_mqtt_service.py
```

### Testing

Run the test suite:

```bash
pytest
```

## License

MIT