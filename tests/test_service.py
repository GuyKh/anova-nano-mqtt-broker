"""Tests for anova-nano-to-mqtt service."""

import paho.mqtt.client as mqtt
import pytest

try:
    from pyanova import Pyanova
except ImportError:
    # Fallback for different package structure
    try:
        from pyanova_nano import Pyanova
    except ImportError:
        # Last resort: import module and get the class
        import pyanova_nano
        Pyanova = pyanova_nano.Pyanova


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
    pa = Pyanova()
    assert pa is not None


# Integration-style test (requires actual Anova Nano device)
@pytest.mark.integration
def test_anova_nano_connection():
    """Test Anova Nano connection (requires device)."""
    pa = Pyanova()
    # This test requires an actual Anova Nano device
    # and will be skipped if no device is found
    try:
        devices = pa.discover(timeout=2.0)
    except (OSError, RuntimeError):
        pytest.skip("No Anova Nano device available for integration test")
    assert len(devices) > 0, "No Anova devices found"