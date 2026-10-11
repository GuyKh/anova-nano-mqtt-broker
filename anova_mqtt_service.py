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
import os
import sys

import paho.mqtt.client as mqtt

# Import the PyAnova class from the pyanova_nano package
try:
    from pyanova_nano import PyAnova as Pyanova
except ImportError:
    # Fallback: try to import from pyanova module (in case of different packaging)
    try:
        from pyanova import PyAnova as Pyanova
    except ImportError:
        # Last resort: import modules and access the class
        import pyanova_nano
        Pyanova = pyanova_nano.PyAnova

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("anova-nano-mqtt")


class AnovaMQTTBridge:
    """Bridge between Anova Nano BLE and MQTT."""

    def __init__(self):
        # Configuration from environment variables with defaults
        self.mqtt_broker = os.environ.get("MQTT_BROKER", "mqtt-broker")
        try:
            self.mqtt_port = int(os.environ.get("MQTT_PORT", "1883"))
        except ValueError:
            self.mqtt_port = 1883
            logger.warning("Invalid MQTT_PORT, using default: 1883")
        self.mqtt_user = os.environ.get("MQTT_USER")  # None if not set
        self.mqtt_password = os.environ.get("MQTT_PASSWORD")  # None if not set
        self.mqtt_topic_prefix = os.environ.get("MQTT_TOPIC_PREFIX", "anova/nano")
        try:
            self.scan_timeout = float(os.environ.get("SCAN_TIMEOUT", "5.0"))
        except ValueError:
            self.scan_timeout = 5.0
            logger.warning("Invalid SCAN_TIMEOUT, using default: 5.0")
        # min_rssi is optional and not required by spec, but we keep it for completeness
        try:
            self.min_rssi = int(os.environ.get("MIN_RSSI", "-100"))
        except ValueError:
            self.min_rssi = -100
            logger.warning("Invalid MIN_RSSI, using default: -100")

        self.mqtt_client = mqtt.Client()
        self.anova = None
        self.device_address = None
        self.running = False

    def setup_mqtt(self):
        """Configure and connect MQTT client."""
        self.mqtt_client.on_connect = self.on_mqtt_connect
        self.mqtt_client.on_publish = self.on_mqtt_publish
        self.mqtt_client.on_disconnect = self.on_mqtt_disconnect

        if self.mqtt_user:
            self.mqtt_client.username_pw_set(self.mqtt_user, self.mqtt_password)

        try:
            self.mqtt_client.connect(self.mqtt_broker, self.mqtt_port, 60)
            self.mqtt_client.loop_start()
            logger.info(f"Connected to MQTT broker at {self.mqtt_broker}:{self.mqtt_port}")
        except (ConnectionRefusedError, OSError) as e:
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
        self.anova = Pyanova()

        # Auto-discover devices
        try:
            devices = self.anova.discover(timeout_seconds=self.scan_timeout)
        except (OSError, RuntimeError) as e:
            logger.error(f"Failed to scan for Anova devices: {e}")
            return False

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
        except (OSError, RuntimeError) as e:
            logger.error(f"Failed to connect to Anova device: {e}")
            return False

    def publish_status(self, status_data: dict):
        """Publish device status to MQTT."""
        topic = f"{self.mqtt_topic_prefix}/status"
        payload = str(status_data)
        self.mqtt_client.publish(topic, payload)
        logger.info(f"Published status to {topic}: {payload}")

    def publish_temperature(self, temp_data: dict):
        """Publish temperature reading to MQTT."""
        topic = f"{self.mqtt_topic_prefix}/temperature"
        payload = str(temp_data)
        self.mqtt_client.publish(topic, payload)
        logger.info(f"Published temperature to {topic}: {payload}")

    def publish_power(self, power_data: dict):
        """Publish power state to MQTT."""
        topic = f"{self.mqtt_topic_prefix}/power"
        payload = str(power_data)
        self.mqtt_client.publish(topic, payload)
        logger.info(f"Published power state to {topic}: {payload}")

    def publish_alert(self, alert_data: dict):
        """Publish alert/warning to MQTT."""
        topic = f"{self.mqtt_topic_prefix}/alert"
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

            except (OSError, RuntimeError) as e:
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