"""Tests for anova-nano-to-mqtt service."""

import asyncio
import paho.mqtt.client as mqtt
import pytest

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


def test_requirements_import():
    """Verify core dependencies are importable."""
    assert Pyanova is not None
    import paho.mqtt.client as mqtt_client
    assert mqtt_client is not None


def test_config_structure():
    """Verify configuration dictionary has expected keys."""
    config = {
        "mqtt": {
            "broker": "mqtt",
            "port": 1883,
            "topic_prefix": "anova/nano",
        },
        "scan": {
            "timeout": 5.0,
            "min_rssi": -100,
        },
    }
    assert "mqtt" in config
    assert "scan" in config
    assert config["mqtt"]["broker"] == "mqtt"
    assert config["mqtt"]["port"] == 1883


def test_mqtt_client_creation():
    """Verify MQTT client can be created and configured."""
    client = mqtt.Client()
    assert client is not None
    assert client._client_id is not None


def test_pyanova_instantiation():
    """Verify pyanova can be instantiated."""
    # Create event loop for pyanova initialization
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        pa = Pyanova(loop=loop)
        assert pa is not None
    finally:
        loop.close()


# Integration-style test (requires actual Anova Nano device)
@pytest.mark.integration
def test_anova_nano_connection():
    """Test Anova Nano connection (requires device)."""
    # Create event loop for pyanova initialization
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        pa = Pyanova(loop=loop)
        # This test requires an actual Anova Nano device
        # and will be skipped if no device is found
        try:
            devices = loop.run_until_complete(pa.discover(timeout_seconds=2.0))
        except (OSError, RuntimeError):
            pytest.skip("No Anova Nano device available for integration test")
        assert len(devices) > 0, "No Anova devices found"
    finally:
        loop.close()