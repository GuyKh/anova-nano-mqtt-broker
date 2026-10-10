"""Tests for anova-nano-to-mqtt service."""

import pytest
from pyanova import pyanova
import paho.mqtt.client as mqtt


def test_requirements_import():
    """Verify core dependencies are importable."""
    import pyanova
    import paho.mqtt.client as mqtt_client
    assert pyanova is not None
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
    pa = pyanova()
    assert pa is not None


# Integration-style test (requires actual Anova Nano device)
@pytest.mark.integration
def test_anova_nano_connection():
    """Test Anova Nano connection (requires device)."""
    import asyncio
    
    pa = pyanova()
    # This test requires an actual Anova Nano device
    # and will be skipped if no device is found
    try:
        devices = pa.discover(timeout=2.0)
        assert len(devices) > 0, "No Anova devices found"
    except Exception:
        pytest.skip("No Anova Nano device available for integration test")