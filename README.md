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

The Dockerfile includes BlueZ for direct Bluetooth access. Depending on your host setup,
you may need:

```bash
# Option 1: Add Bluetooth group
docker compose up -d --group-add bluetooth

# Option 2: Use privileged mode (less secure)
docker compose up -d
```

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