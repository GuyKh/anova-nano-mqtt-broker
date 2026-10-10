#!/usr/bin/env python3
"""Anova Nano BLE to MQTT Bridge Service.

Uses pyanova-nano to connect to Anova Nano via BLE and publish
sensor events to an MQTT broker.

Usage:
    docker compose up -d
    # or run directly:
    python anova_mqtt_service.py
"""

import asyncio
import logging
import sys

import paho.mqtt.client as mqtt
from pyanova import pyanova

# Configuration
CONFIG = {
    "mqtt": {
        "broker": "mqtt",
        "port": 1883,
        "topic_prefix": "anova/nano",
        "username": None,
        "password": None,
    },
    "scan": {
        "timeout": 5.0,  # seconds to scan for devices
        "min_rssi": -100,  # minimum RSSI to consider a device
    },
}

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("anova-nano-mqtt")


class AnovaMQTTBridge:
    """Bridge between Anova Nano BLE and MQTT."""

    def __init__(self, config: dict | None = None):
        self.config = config or CONFIG
        self.mqtt_client = mqtt.Client()
        self.anova = None
        self.device_address = None
        self.running = False

    def setup_mqtt(self):
        """Configure and connect MQTT client."""
        broker = self.config["mqtt"]["broker"]
        port = self.config["mqtt"]["port"]
        self.config["mqtt"]["topic_prefix"]

        self.mqtt_client.on_connect = self.on_mqtt_connect
        self.mqtt_client.on_publish = self.on_mqtt_publish
        self.mqtt_client.on_disconnect = self.on_mqtt_disconnect

        if self.config["mqtt"]["username"]:
            self.mqtt_client.username_pw_set(
                self.config["mqtt"]["username"],
                self.config["mqtt"]["password"],
            )

        try:
            self.mqtt_client.connect(broker, port, 60)
            self.mqtt_client.loop_start()
            logger.info(f"Connected to MQTT broker at {broker}:{port}")
        except Exception as e:
            logger.error(f"Failed to connect to MQTT broker: {e}")
            raise

    def on_mqtt_connect(self, client, userdata, flags, rc):
        """Callback when MQTT connects."""
        if rc == 0:
            logger.info("MQTT connected successfully")
        else:
            logger.error(f"MQTT connection failed with code: {rc}")

    def on_mqtt_publish(self, client, userdata, mid):
        """Callback when message is published."""
        logger.debug(f"Message published: mid={mid}")

    def on_mqtt_disconnect(self, client, userdata, rc):
        """Callback when MQTT disconnects."""
        logger.warning(f"MQTT disconnected with code: {rc}")

    def get_anova_device(self):
        """Discover and connect to Anova Nano device."""
        logger.info("Scanning for Anova Nano devices...")

        # Create pyanova instance
        self.anova = pyanova()

        # Auto-discover devices
        devices = self.anova.discover(timeout=self.config["scan"]["timeout"])

        if not devices:
            logger.warning("No Anova devices found during scan")
            return False

        # Use the first device found
        device = devices[0]
        self.device_address = device.address
        logger.info(f"Found Anova device: {device.address}")

        # Connect to the device
        try:
            self.anova.connect_device(device=device)
            logger.info(f"Connected to Anova Nano at {device.address}")
            return True
        except Exception as e:
            logger.error(f"Failed to connect to Anova device: {e}")
            return False

    def publish_status(self, status_data: dict):
        """Publish device status to MQTT."""
        topic = f"{self.config['mqtt']['topic_prefix']}/status"
        payload = str(status_data)
        self.mqtt_client.publish(topic, payload)
        logger.info(f"Published status to {topic}: {payload}")

    def publish_temperature(self, temp_data: dict):
        """Publish temperature reading to MQTT."""
        topic = f"{self.config['mqtt']['topic_prefix']}/temperature"
        payload = str(temp_data)
        self.mqtt_client.publish(topic, payload)
        logger.info(f"Published temperature to {topic}: {payload}")

    def publish_power(self, power_data: dict):
        """Publish power state to MQTT."""
        topic = f"{self.config['mqtt']['topic_prefix']}/power"
        payload = str(power_data)
        self.mqtt_client.publish(topic, payload)
        logger.info(f"Published power state to {topic}: {payload}")

    def publish_alert(self, alert_data: dict):
        """Publish alert/warning to MQTT."""
        topic = f"{self.config['mqtt']['topic_prefix']}/alert"
        payload = str(alert_data)
        self.mqtt_client.publish(topic, payload)
        logger.info(f"Published alert to {topic}: {payload}")

    async def read_loop(self):
        """Main read loop - continuously read from Anova Nano and publish to MQTT."""
        logger.info("Starting read loop...")

        while self.running:
            try:
                # Get current status
                status = self.anova.get_status()
                logger.debug(f"Got status: {status}")

                # Publish various data points
                if hasattr(status, "temperature"):
                    self.publish_temperature(
                        {"temperature": status.temperature, "unit": "celsius"}
                    )

                if hasattr(status, "power"):
                    self.publish_power(
                        {"power": status.power, "is_cooking": status.is_cooking}
                    )

                if hasattr(status, "target_temperature"):
                    # Publish target/temp setpoint
                    pass  # Could publish target temperature

                # Small delay between reads
                await asyncio.sleep(5)

            except Exception as e:
                logger.error(f"Error in read loop: {e}")
                await asyncio.sleep(2)  # Back off before retry

    async def run(self):
        """Run the bridge service."""
        self.running = True

        # Setup MQTT
        self.setup_mqtt()

        # Connect to Anova Nano
        if not self.get_anova_device():
            logger.error("Failed to connect to Anova Nano. Exiting.")
            return

        # Start the read loop
        try:
            await self.read_loop()
        except KeyboardInterrupt:
            logger.info("Received KeyboardInterrupt, shutting down...")
        finally:
            self.running = False
            if self.anova:
                self.anova.disconnect()
            self.mqtt_client.loop_stop()
            self.mqtt_client.disconnect()
            logger.info("Bridge shutdown complete")


def main():
    """Main entry point."""
    bridge = AnovaMQTTBridge()
    try:
        asyncio.run(bridge.run())
    except KeyboardInterrupt:
        logger.info("Exiting...")


if __name__ == "__main__":
    main()