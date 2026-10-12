"""Tests for the Anova Nano MQTT bridge.

The fake device implements the subset of pyanova_nano.PyAnova the bridge uses;
test_fake_matches_pyanova_api() fails if those names drift from the real class.
"""

from __future__ import annotations

import asyncio
import inspect
import json
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest
from pyanova_nano import PyAnova
from pyanova_nano.types import ReadCommands, SensorValues

import anova_mqtt_service as svc
from anova_mqtt_service import (
    AnovaMQTTBridge,
    Command,
    Config,
    build_state,
    discovery_messages,
    parse_command,
)

ADDRESS = "AA:BB:CC:DD:EE:FF"


def sensors(**overrides: Any) -> SensorValues:
    values: dict[str, Any] = {
        "water_temp": 58.23,
        "water_temp_units": "C",
        "heater_temp": 61.1,
        "heater_temp_units": "C",
        "triac_temp": 45.12,
        "triac_temp_units": "C",
        "internal_temp": 35.05,
        "internal_temp_units": "C",
        "water_low": False,
        "water_leak": False,
        "motor_speed": 1800,
    }
    values.update(overrides)
    return SensorValues(**values)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


@dataclass
class FakeAnova:
    """Stands in for PyAnova. Records calls; can simulate a BLE disconnect."""

    sensor_values: SensorValues = field(default_factory=sensors)
    target: float = 60.0
    timer: int = 90
    unit: str = "C"
    fail_on: str | None = None
    calls: list[tuple[str, Any]] = field(default_factory=list)
    connected: bool = False
    _disconnect_cbs: list = field(default_factory=list)

    def _maybe_fail(self, name: str) -> None:
        if self.fail_on == name:
            raise TimeoutError(f"simulated {name} timeout")

    async def connect(self, device=None, timeout_seconds=None):
        self.calls.append(("connect", device.address))
        self.connected = True

    async def disconnect(self):
        self.calls.append(("disconnect", None))
        self.connected = False

    def is_connected(self) -> bool:
        return self.connected

    def add_on_disconnect(self, cb):
        self._disconnect_cbs.append(cb)
        return lambda: self._disconnect_cbs.remove(cb)

    def drop_link(self) -> None:
        self.connected = False
        for cb in list(self._disconnect_cbs):
            cb()

    async def get_sensor_values(self):
        self._maybe_fail("get_sensor_values")
        return self.sensor_values

    async def get_target_temperature(self):
        return self.target

    async def get_timer(self):
        return self.timer

    async def get_unit(self):
        return self.unit

    async def start(self):
        self.calls.append(("start", None))
        self.sensor_values = sensors(motor_speed=1800)

    async def stop(self):
        self.calls.append(("stop", None))
        self.sensor_values = sensors(motor_speed=0)

    async def set_target_temperature(self, temperature: float):
        self.calls.append(("set_target_temperature", temperature))
        self.target = temperature

    async def set_timer(self, time_minutes: int):
        self.calls.append(("set_timer", time_minutes))
        self.timer = time_minutes

    async def set_unit(self, unit: str):
        self.calls.append(("set_unit", unit))
        self.unit = unit

    async def send_read_command(self, command):
        assert command == ReadCommands.GetFirmwareInfo
        return SimpleNamespace(tagId="v1.2.3", commitId="abc123", dateCode=20240101)

    async def get_device_info(self):
        return SimpleNamespace(value=7)


class FakeMQTT:
    """Minimal paho Client stand-in."""

    def __init__(self) -> None:
        self.published: list[tuple[str, str, int, bool]] = []
        self.subscribed: list[str] = []
        self.will: tuple | None = None
        self.on_connect = self.on_disconnect = self.on_message = None

    def username_pw_set(self, user, password):
        self.credentials = (user, password)

    def will_set(self, topic, payload, qos, retain):
        self.will = (topic, payload, qos, retain)

    def reconnect_delay_set(self, min_delay, max_delay):
        pass

    def connect_async(self, host, port, keepalive):
        self.target = (host, port)

    def loop_start(self):
        pass

    def loop_stop(self):
        pass

    def disconnect(self):
        pass

    def subscribe(self, topic, qos=0):
        self.subscribed.append(topic)

    def publish(self, topic, payload, qos=0, retain=False):
        self.published.append((topic, payload, qos, retain))

    # helpers
    def last(self, topic: str) -> str | None:
        for t, p, _, _ in reversed(self.published):
            if t == topic:
                return p
        return None

    def deliver(self, topic: str, payload: str) -> None:
        self.on_message(
            self, None, SimpleNamespace(topic=topic, payload=payload.encode())
        )

    def connack(self) -> None:
        self.on_connect(self, None, None, SimpleNamespace(is_failure=False), None)


def make_bridge(
    anovas: list[FakeAnova], **cfg: Any
) -> tuple[AnovaMQTTBridge, FakeMQTT]:
    config = Config(
        poll_interval=cfg.pop("poll_interval", 3600),
        reconnect_min=0.01,
        reconnect_max=0.02,
        **cfg,
    )
    mqttc = FakeMQTT()
    it = iter(anovas)

    async def find(_cfg, _anova):
        return SimpleNamespace(address=ADDRESS, name="Nano")

    bridge = AnovaMQTTBridge(
        config, mqtt_client=mqttc, anova_factory=lambda: next(it), find_device=find
    )
    return bridge, mqttc


async def wait_for(predicate, timeout: float = 3.0) -> None:
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while not predicate():
        if loop.time() > end:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.01)


async def run_with(bridge: AnovaMQTTBridge, scenario) -> None:
    task = asyncio.create_task(bridge.run())
    try:
        await scenario()
    finally:
        bridge.stop()
        await asyncio.wait_for(task, timeout=5)


# ---------------------------------------------------------------------------
# API parity with pyanova-nano
# ---------------------------------------------------------------------------


def test_fake_matches_pyanova_api():
    used = [
        "connect",
        "disconnect",
        "is_connected",
        "add_on_disconnect",
        "get_sensor_values",
        "get_target_temperature",
        "get_timer",
        "get_unit",
        "start",
        "stop",
        "set_target_temperature",
        "set_timer",
        "set_unit",
        "send_read_command",
        "get_device_info",
        "discover",
    ]
    for name in used:
        assert hasattr(PyAnova, name), f"PyAnova has no {name}()"
    # Parameter names the bridge relies on.
    assert "device" in inspect.signature(PyAnova.connect).parameters
    assert "timeout_seconds" in inspect.signature(PyAnova.discover).parameters
    assert hasattr(ReadCommands, "GetFirmwareInfo")


def test_build_state_has_every_sensorvalues_field():
    sv = sensors(motor_speed=0, water_low=True)
    state = build_state(sv, 60.5, 45, "C")
    for f in SensorValues.__dataclass_fields__:
        assert state[f] == getattr(sv, f)
    assert state["target_temperature"] == 60.5
    assert state["timer"] == 45
    assert state["unit"] == "C"
    # PyAnova.get_status(): running iff motor_speed != 0
    assert state["running"] is False and state["status"] == "stopped"
    assert build_state(sensors(motor_speed=1), 0, 0, "C")["status"] == "running"
    json.dumps(state)  # must be JSON-serializable


# ---------------------------------------------------------------------------
# Command parsing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("payload", ["ON", "on", "true", "1", "start", "heat"])
def test_power_on(payload):
    assert parse_command("power", payload, "C") == Command("power", True)


@pytest.mark.parametrize("payload", ["OFF", "false", "0", "stop", "off"])
def test_power_off(payload):
    assert parse_command("mode", payload, "C") == Command("power", False)


def test_target_temperature_validated_in_device_unit():
    assert parse_command("target_temperature", "57.5", "C") == Command(
        "target_temperature", 57.5
    )
    assert parse_command("target_temperature", "135", "F") == Command(
        "target_temperature", 135.0
    )
    with pytest.raises(ValueError):
        parse_command("target_temperature", "135", "C")  # 135 °C is out of range
    with pytest.raises(ValueError):
        parse_command("target_temperature", "20", "F")
    with pytest.raises(ValueError):
        parse_command("target_temperature", "abc", "C")
    with pytest.raises(ValueError, match="unit not known"):
        parse_command("target_temperature", "60", None)


def test_timer_and_unit():
    assert parse_command("timer", "90", "C") == Command("timer", 90)
    assert parse_command("timer", "0", "C") == Command("timer", 0)
    for bad in ("-1", "1.5", "6000", "x"):
        with pytest.raises(ValueError):
            parse_command("timer", bad, "C")
    assert parse_command("unit", "f", "C") == Command("unit", "F")
    with pytest.raises(ValueError):
        parse_command("unit", "K", "C")
    with pytest.raises(ValueError):
        parse_command("explode", "1", "C")


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def test_discovery_payloads():
    cfg = Config()
    state = build_state(sensors(water_temp_units="F"), 140.0, 0, "F")
    msgs = discovery_messages(cfg, ADDRESS, state, {"firmware_version": "v1"})
    ids = [m["unique_id"] for m in msgs.values()]
    assert len(ids) == len(set(ids))
    climate = msgs["homeassistant/climate/anova_nano_aabbccddeeff/climate/config"]
    assert climate["temperature_unit"] == "F"
    assert (climate["min_temp"], climate["max_temp"]) == (32.0, 197.0)
    assert climate["mode_command_topic"] == "anova/nano/set/mode"
    assert climate["temperature_command_topic"] == "anova/nano/set/target_temperature"
    assert climate["availability_topic"] == "anova/nano/availability"
    assert climate["device"]["sw_version"] == "v1"
    water = msgs["homeassistant/sensor/anova_nano_aabbccddeeff/water_temp/config"]
    assert water["unit_of_measurement"] == "°F"
    for payload in msgs.values():
        json.dumps(payload)


# ---------------------------------------------------------------------------
# End-to-end with fakes
# ---------------------------------------------------------------------------


def test_mqtt_setup():
    bridge, mqttc = make_bridge([], mqtt_user="u", mqtt_password="p")
    bridge.setup_mqtt()
    assert mqttc.will == ("anova/nano/availability", "offline", 1, True)
    assert mqttc.credentials == ("u", "p")
    mqttc.connack()
    assert "anova/nano/set/+" in mqttc.subscribed
    assert "homeassistant/status" in mqttc.subscribed


def test_session_publishes_state_info_and_executes_commands():
    async def main():
        dev = FakeAnova()
        bridge, mqttc = make_bridge([dev])

        async def scenario():
            await wait_for(lambda: mqttc.last("anova/nano/availability") == "online")
            state = json.loads(mqttc.last("anova/nano/state"))
            assert state["water_temp"] == 58.23 and state["running"] is True
            info = json.loads(mqttc.last("anova/nano/info"))
            assert info == {
                "address": ADDRESS,
                "firmware_version": "v1.2.3",
                "firmware_commit": "abc123",
                "firmware_date": 20240101,
                "device_info": 7,
            }
            assert any(
                t.startswith("homeassistant/climate/") for t, *_ in mqttc.published
            )

            mqttc.deliver("anova/nano/set/target_temperature", "57.5")
            mqttc.deliver("anova/nano/set/timer", "120")
            mqttc.deliver("anova/nano/set/power", "OFF")
            mqttc.deliver("anova/nano/set/target_temperature", "500")  # rejected
            await wait_for(lambda: ("stop", None) in dev.calls)
            await wait_for(
                lambda: (
                    json.loads(mqttc.last("anova/nano/state"))["status"] == "stopped"
                )
            )
            state = json.loads(mqttc.last("anova/nano/state"))
            assert state["target_temperature"] == 57.5 and state["timer"] == 120
            assert ("set_target_temperature", 500.0) not in dev.calls

        await run_with(bridge, scenario)
        assert mqttc.last("anova/nano/availability") == "offline"
        assert ("disconnect", None) in dev.calls

    asyncio.run(main())


def test_reconnects_after_ble_drop_and_drops_stale_commands():
    async def main():
        first, second = FakeAnova(), FakeAnova()
        bridge, mqttc = make_bridge([first, second])

        async def scenario():
            await wait_for(lambda: mqttc.last("anova/nano/availability") == "online")
            first.drop_link()
            await wait_for(lambda: mqttc.last("anova/nano/availability") == "offline")
            # Arrives while offline: must not be replayed on the new connection.
            mqttc.deliver("anova/nano/set/power", "ON")
            await wait_for(lambda: ("connect", ADDRESS) in second.calls)
            await wait_for(lambda: mqttc.last("anova/nano/availability") == "online")
            await asyncio.sleep(0.05)
            assert ("start", None) not in first.calls + second.calls

        await run_with(bridge, scenario)

    asyncio.run(main())


def test_poll_failure_triggers_reconnect():
    async def main():
        flaky, good = FakeAnova(), FakeAnova()
        bridge, mqttc = make_bridge([flaky, good], poll_interval=1.0)

        async def scenario():
            await wait_for(lambda: mqttc.last("anova/nano/availability") == "online")
            flaky.fail_on = "get_sensor_values"
            await wait_for(lambda: ("connect", ADDRESS) in good.calls, timeout=5)
            await wait_for(lambda: mqttc.last("anova/nano/availability") == "online")

        await run_with(bridge, scenario)

    asyncio.run(main())


def test_device_not_found_retries():
    async def main():
        attempts = 0
        config = Config(reconnect_min=0.01, reconnect_max=0.02)
        mqttc = FakeMQTT()

        async def find(_cfg, _anova):
            nonlocal attempts
            attempts += 1

        bridge = AnovaMQTTBridge(
            config, mqtt_client=mqttc, anova_factory=FakeAnova, find_device=find
        )

        async def scenario():
            await wait_for(lambda: attempts >= 3)

        await run_with(bridge, scenario)
        assert mqttc.last("anova/nano/availability") == "offline"

    asyncio.run(main())


def test_config_from_env(monkeypatch):
    monkeypatch.setenv("MQTT_PORT", "nope")
    monkeypatch.setenv("MQTT_TOPIC_PREFIX", "kitchen/anova/")
    monkeypatch.setenv("POLL_INTERVAL", "10")
    monkeypatch.setenv("HA_DISCOVERY", "false")
    monkeypatch.setenv("MQTT_USER", "")
    cfg = Config.from_env()
    assert cfg.mqtt_port == 1883
    assert cfg.topic_prefix == "kitchen/anova"
    assert cfg.poll_interval == 10.0
    assert cfg.ha_discovery is False
    assert cfg.mqtt_user is None


def test_module_entrypoint_exists():
    assert callable(svc.main)
