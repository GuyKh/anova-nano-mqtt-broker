#!/usr/bin/env python3
"""Anova Nano BLE <-> MQTT bridge.

Uses pyanova-nano (https://github.com/filmkorn/pyanova-nano) for all device
communication; this module only maps PyAnova's API onto MQTT topics.

Topics (prefix defaults to ``anova/nano``):

    <prefix>/availability        "online" / "offline" (retained, LWT)
    <prefix>/state               JSON device state (retained), see build_state()
    <prefix>/info                JSON firmware/device info (retained)
    <prefix>/set/power           ON|OFF (also start|stop, heat|off, true|false, 1|0)
    <prefix>/set/mode            heat|off (Home Assistant climate mode)
    <prefix>/set/target_temperature   float, in the cooker's current unit
    <prefix>/set/timer           integer minutes
    <prefix>/set/unit            C|F

Usage:
    docker compose up -d
    # or:
    python anova_mqtt_service.py
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import json
import logging
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion
from pyanova_nano import PyAnova
from pyanova_nano.types import ReadCommands, SensorValues

logger = logging.getLogger("anova-nano-mqtt")

# Anova Nano setpoint range (32-197 °F / 0-92 °C), in the cooker's current unit.
TEMPERATURE_RANGE = {"C": (0.0, 92.0), "F": (32.0, 197.0)}
TIMER_MAX_MINUTES = 5999  # 99 h 59 min

_TRUE = {"on", "true", "1", "start", "heat", "yes"}
_FALSE = {"off", "false", "0", "stop", "no"}


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def _env_str(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    return value if value not in (None, "") else default


def _env_num(name: str, default: float, cast: Callable[[str], Any] = float) -> Any:
    raw = _env_str(name)
    if raw is None:
        return cast(str(default))
    try:
        return cast(raw)
    except ValueError:
        logger.warning("Invalid %s=%r, using default %s", name, raw, default)
        return cast(str(default))


def _env_bool(name: str, default: bool) -> bool:
    raw = _env_str(name)
    if raw is None:
        return default
    if raw.lower() in _TRUE:
        return True
    if raw.lower() in _FALSE:
        return False
    logger.warning("Invalid %s=%r, using default %s", name, raw, default)
    return default


@dataclass(frozen=True)
class Config:
    mqtt_broker: str = "mqtt-broker"
    mqtt_port: int = 1883
    mqtt_user: str | None = None
    mqtt_password: str | None = None
    mqtt_client_id: str = "anova-nano-bridge"
    topic_prefix: str = "anova/nano"
    device_address: str | None = None  # BLE MAC; discover if unset
    scan_timeout: float = 10.0
    poll_interval: float = 30.0  # PyAnova's default poll interval
    reconnect_min: float = 5.0
    reconnect_max: float = 300.0
    ha_discovery: bool = True
    ha_discovery_prefix: str = "homeassistant"
    device_name: str = "Anova Nano"

    @classmethod
    def from_env(cls) -> Config:
        return cls(
            mqtt_broker=_env_str("MQTT_BROKER", cls.mqtt_broker) or cls.mqtt_broker,
            mqtt_port=_env_num("MQTT_PORT", cls.mqtt_port, int),
            mqtt_user=_env_str("MQTT_USER"),
            mqtt_password=_env_str("MQTT_PASSWORD"),
            mqtt_client_id=_env_str("MQTT_CLIENT_ID", cls.mqtt_client_id)
            or cls.mqtt_client_id,
            topic_prefix=(
                _env_str("MQTT_TOPIC_PREFIX", cls.topic_prefix) or cls.topic_prefix
            ).rstrip("/"),
            device_address=_env_str("DEVICE_ADDRESS"),
            scan_timeout=_env_num("SCAN_TIMEOUT", cls.scan_timeout),
            poll_interval=max(1.0, _env_num("POLL_INTERVAL", cls.poll_interval)),
            reconnect_min=_env_num("RECONNECT_MIN", cls.reconnect_min),
            reconnect_max=_env_num("RECONNECT_MAX", cls.reconnect_max),
            ha_discovery=_env_bool("HA_DISCOVERY", cls.ha_discovery),
            ha_discovery_prefix=_env_str("HA_DISCOVERY_PREFIX", cls.ha_discovery_prefix)
            or cls.ha_discovery_prefix,
            device_name=_env_str("DEVICE_NAME", cls.device_name) or cls.device_name,
        )


# ---------------------------------------------------------------------------
# Pure helpers (unit-tested)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Command:
    name: str  # "power" | "target_temperature" | "timer" | "unit"
    value: Any


def parse_bool(payload: str) -> bool:
    p = payload.strip().lower()
    if p in _TRUE:
        return True
    if p in _FALSE:
        return False
    raise ValueError(f"expected ON/OFF, got {payload!r}")


def parse_command(suffix: str, payload: str, unit: str | None) -> Command:
    """Validate an MQTT command. ``unit`` is the cooker's current unit, if known.

    Raises ValueError with a human-readable reason on invalid input.
    """
    payload = payload.strip()
    if suffix in ("power", "mode"):
        return Command("power", parse_bool(payload))
    if suffix == "target_temperature":
        if unit is None:
            raise ValueError("device unit not known yet; refusing to set a temperature")
        try:
            value = float(payload)
        except ValueError:
            raise ValueError(f"not a number: {payload!r}") from None
        low, high = TEMPERATURE_RANGE[unit]
        if not low <= value <= high:
            raise ValueError(f"{value} °{unit} outside {low}-{high} °{unit}")
        return Command("target_temperature", value)
    if suffix == "timer":
        try:
            minutes = float(payload)
        except ValueError:
            raise ValueError(f"not a number: {payload!r}") from None
        if not minutes.is_integer() or not 0 <= minutes <= TIMER_MAX_MINUTES:
            raise ValueError(
                f"timer must be whole minutes 0-{TIMER_MAX_MINUTES}, got {payload!r}"
            )
        return Command("timer", int(minutes))
    if suffix == "unit":
        u = payload.upper()
        if u not in ("C", "F"):
            raise ValueError(f"unit must be C or F, got {payload!r}")
        return Command("unit", u)
    raise ValueError(f"unknown command {suffix!r}")


def build_state(
    sensors: SensorValues, target_temperature: float, timer: int, unit: str
) -> dict[str, Any]:
    """Device state as published on <prefix>/state.

    Contains every field of pyanova-nano's SensorValues, plus the setpoint
    (get_target_temperature, in ``unit``), the timer (get_timer, minutes), the
    display unit (get_unit) and the run status (get_status).
    """
    state: dict[str, Any] = dataclasses.asdict(sensors)
    running = sensors.motor_speed != 0  # PyAnova.get_status()
    state.update(
        target_temperature=target_temperature,
        unit=unit,
        timer=timer,
        running=running,
        status="running" if running else "stopped",
    )
    return state


def discovery_messages(
    cfg: Config, address: str, state: dict[str, Any], info: dict[str, Any]
) -> dict[str, dict]:
    """Home Assistant MQTT discovery configs, keyed by config topic."""
    node = "anova_nano_" + address.replace(":", "").lower()
    p = cfg.topic_prefix
    unit = state.get("unit", "C")
    low, high = TEMPERATURE_RANGE.get(unit, TEMPERATURE_RANGE["C"])
    device = {
        "identifiers": [node],
        "connections": [["bluetooth", address]],
        "name": cfg.device_name,
        "manufacturer": "Anova",
        "model": "Nano",
    }
    if info.get("firmware_version"):
        device["sw_version"] = info["firmware_version"]
    common = {
        "availability_topic": f"{p}/availability",
        "device": device,
    }

    def topic(component: str, key: str) -> str:
        return f"{cfg.ha_discovery_prefix}/{component}/{node}/{key}/config"

    def entity(key: str, name: str | None, **extra: Any) -> dict[str, Any]:
        return {**common, "name": name, "unique_id": f"{node}_{key}", **extra}

    def temp_sensor(key: str, name: str, diagnostic: bool) -> dict[str, Any]:
        cfg_ = entity(
            key,
            name,
            state_topic=f"{p}/state",
            value_template=f"{{{{ value_json.{key} }}}}",
            device_class="temperature",
            state_class="measurement",
            unit_of_measurement="°" + state.get(f"{key}_units", unit),
            suggested_display_precision=1,
        )
        if diagnostic:
            cfg_["entity_category"] = "diagnostic"
        return cfg_

    msgs: dict[str, dict] = {
        topic("climate", "climate"): entity(
            "climate",
            None,  # use the device name
            modes=["off", "heat"],
            mode_command_topic=f"{p}/set/mode",
            mode_state_topic=f"{p}/state",
            mode_state_template="{{ 'heat' if value_json.running else 'off' }}",
            action_topic=f"{p}/state",
            action_template="{{ 'heating' if value_json.running else 'off' }}",
            temperature_command_topic=f"{p}/set/target_temperature",
            temperature_state_topic=f"{p}/state",
            temperature_state_template="{{ value_json.target_temperature }}",
            current_temperature_topic=f"{p}/state",
            current_temperature_template="{{ value_json.water_temp }}",
            temperature_unit=unit,
            min_temp=low,
            max_temp=high,
            temp_step=0.1,
            precision=0.1,
        ),
        topic("switch", "cooking"): entity(
            "cooking",
            "Cooking",
            icon="mdi:pot-steam",
            command_topic=f"{p}/set/power",
            state_topic=f"{p}/state",
            value_template="{{ 'ON' if value_json.running else 'OFF' }}",
        ),
        topic("sensor", "water_temp"): temp_sensor(
            "water_temp", "Water temperature", False
        ),
        topic("sensor", "heater_temp"): temp_sensor(
            "heater_temp", "Heater temperature", True
        ),
        topic("sensor", "triac_temp"): temp_sensor(
            "triac_temp", "Triac temperature", True
        ),
        topic("sensor", "internal_temp"): temp_sensor(
            "internal_temp", "Internal temperature", True
        ),
        topic("sensor", "motor_speed"): entity(
            "motor_speed",
            "Motor speed",
            icon="mdi:fan",
            state_topic=f"{p}/state",
            value_template="{{ value_json.motor_speed }}",
            state_class="measurement",
            entity_category="diagnostic",
        ),
        topic("sensor", "timer"): entity(
            "timer",
            "Timer remaining",
            icon="mdi:timer-outline",
            state_topic=f"{p}/state",
            value_template="{{ value_json.timer }}",
            device_class="duration",
            unit_of_measurement="min",
        ),
        topic("binary_sensor", "water_low"): entity(
            "water_low",
            "Water low",
            device_class="problem",
            state_topic=f"{p}/state",
            value_template="{{ 'ON' if value_json.water_low else 'OFF' }}",
        ),
        topic("binary_sensor", "water_leak"): entity(
            "water_leak",
            "Water leak",
            device_class="moisture",
            state_topic=f"{p}/state",
            value_template="{{ 'ON' if value_json.water_leak else 'OFF' }}",
        ),
        topic("number", "timer_set"): entity(
            "timer_set",
            "Cooking timer",
            icon="mdi:timer-cog-outline",
            command_topic=f"{p}/set/timer",
            state_topic=f"{p}/state",
            value_template="{{ value_json.timer }}",
            min=0,
            max=TIMER_MAX_MINUTES,
            step=1,
            mode="box",
            unit_of_measurement="min",
        ),
        topic("select", "unit"): entity(
            "unit",
            "Display unit",
            icon="mdi:thermometer",
            command_topic=f"{p}/set/unit",
            state_topic=f"{p}/state",
            value_template="{{ value_json.unit }}",
            options=["C", "F"],
            entity_category="config",
        ),
    }
    return msgs


# ---------------------------------------------------------------------------
# Bridge
# ---------------------------------------------------------------------------


class DeviceDisconnected(Exception):
    """The BLE link dropped during a session."""


class AnovaMQTTBridge:
    def __init__(
        self,
        config: Config,
        mqtt_client: mqtt.Client | None = None,
        anova_factory: Callable[[], PyAnova] | None = None,
        find_device: Callable[[Config, PyAnova], Any] | None = None,
    ) -> None:
        self.cfg = config
        self.mqtt = mqtt_client or mqtt.Client(
            callback_api_version=CallbackAPIVersion.VERSION2,
            client_id=config.mqtt_client_id,
        )
        self._anova_factory = anova_factory or PyAnova
        self._find_device = find_device or _find_device
        self._loop: asyncio.AbstractEventLoop | None = None
        self._commands: asyncio.Queue[tuple[str, str]] = asyncio.Queue()
        self._stop = asyncio.Event()
        self._online = False
        self._address: str | None = None
        self._state: dict[str, Any] = {}
        self._info: dict[str, Any] = {}
        self._discovery_signature: str | None = None

    # ---- topics ----
    def t(self, suffix: str) -> str:
        return f"{self.cfg.topic_prefix}/{suffix}"

    # ---- MQTT ----
    def setup_mqtt(self) -> None:
        self.mqtt.on_connect = self._on_mqtt_connect
        self.mqtt.on_disconnect = self._on_mqtt_disconnect
        self.mqtt.on_message = self._on_mqtt_message
        if self.cfg.mqtt_user:
            self.mqtt.username_pw_set(self.cfg.mqtt_user, self.cfg.mqtt_password)
        self.mqtt.will_set(self.t("availability"), "offline", qos=1, retain=True)
        self.mqtt.reconnect_delay_set(min_delay=1, max_delay=60)
        # connect_async + loop_start: paho keeps retrying if the broker isn't up yet.
        self.mqtt.connect_async(self.cfg.mqtt_broker, self.cfg.mqtt_port, keepalive=60)
        self.mqtt.loop_start()
        logger.info(
            "Connecting to MQTT broker %s:%s", self.cfg.mqtt_broker, self.cfg.mqtt_port
        )

    def _on_mqtt_connect(
        self, client, userdata, flags, reason_code, properties
    ) -> None:
        if reason_code.is_failure:
            logger.error("MQTT connection refused: %s", reason_code)
            return
        logger.info("MQTT connected")
        client.subscribe(self.t("set/+"), qos=1)
        if self.cfg.ha_discovery:
            client.subscribe(f"{self.cfg.ha_discovery_prefix}/status", qos=1)
        # Re-assert retained topics after a broker restart.
        self._threadsafe(self._republish)

    def _on_mqtt_disconnect(
        self, client, userdata, flags, reason_code, properties
    ) -> None:
        if reason_code.is_failure:
            logger.warning("MQTT disconnected: %s (paho will reconnect)", reason_code)

    def _on_mqtt_message(self, client, userdata, msg) -> None:
        payload = msg.payload.decode("utf-8", errors="replace")
        if msg.topic == f"{self.cfg.ha_discovery_prefix}/status":
            if payload == "online":  # Home Assistant restarted
                self._threadsafe(self._republish)
            return
        prefix = self.t("set/")
        if msg.topic.startswith(prefix):
            item = (msg.topic[len(prefix) :], payload)
            self._threadsafe(lambda: self._commands.put_nowait(item))

    def _threadsafe(self, fn: Callable[[], Any]) -> None:
        """Run fn on the asyncio loop (paho callbacks run on paho's thread)."""
        if self._loop is not None and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(fn)

    def _publish_json(self, suffix: str, data: dict[str, Any]) -> None:
        self.mqtt.publish(
            self.t(suffix), json.dumps(data, separators=(",", ":")), qos=0, retain=True
        )

    def _set_online(self, online: bool) -> None:
        self._online = online
        self.mqtt.publish(
            self.t("availability"),
            "online" if online else "offline",
            qos=1,
            retain=True,
        )

    def _republish(self) -> None:
        self._set_online(self._online)
        if self._info:
            self._publish_json("info", self._info)
        if self._state:
            self._publish_json("state", self._state)
        self._discovery_signature = None
        self._publish_discovery()

    def _publish_discovery(self) -> None:
        if not (self.cfg.ha_discovery and self._address and self._state):
            return
        msgs = discovery_messages(self.cfg, self._address, self._state, self._info)
        signature = json.dumps(msgs, sort_keys=True)
        if signature == self._discovery_signature:
            return  # unchanged (e.g. units didn't change)
        for topic, payload in msgs.items():
            self.mqtt.publish(topic, json.dumps(payload), qos=1, retain=True)
        self._discovery_signature = signature
        logger.info("Published Home Assistant discovery (%d entities)", len(msgs))

    # ---- BLE session ----
    async def run(self) -> None:
        self._loop = asyncio.get_running_loop()
        self.setup_mqtt()
        delay = self.cfg.reconnect_min
        try:
            while not self._stop.is_set():
                try:
                    await self._session()
                    delay = self.cfg.reconnect_min  # clean session end: reset backoff
                except asyncio.CancelledError:
                    raise
                except (
                    Exception
                ) as exc:  # keep the daemon alive on any BLE/protocol error
                    logger.warning("BLE session ended: %s: %s", type(exc).__name__, exc)
                    logger.debug("Session error", exc_info=True)
                if self._stop.is_set():
                    break
                logger.info("Reconnecting in %.0f s", delay)
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self._stop.wait(), timeout=delay)
                delay = min(delay * 2, self.cfg.reconnect_max)
        finally:
            self._set_online(False)
            self.mqtt.loop_stop()
            self.mqtt.disconnect()
            logger.info("Bridge stopped")

    def stop(self) -> None:
        self._stop.set()

    async def _session(self) -> None:
        anova = self._anova_factory()
        device = await self._find_device(self.cfg, anova)
        if device is None:
            raise RuntimeError("Anova Nano not found")
        logger.info(
            "Connecting to %s (%s)", device.address, getattr(device, "name", None)
        )
        await anova.connect(device=device)

        disconnected = asyncio.Event()
        unsubscribe = anova.add_on_disconnect(
            lambda: self._threadsafe(disconnected.set)
        )
        self._address = device.address
        try:
            await self._read_info(anova)
            await self._refresh(anova)
            self._drop_stale_commands()
            self._set_online(True)
            self._publish_discovery()
            logger.info("Bridge online for %s", device.address)
            await self._serve(anova, disconnected)
        finally:
            self._set_online(False)
            unsubscribe()
            with contextlib.suppress(Exception):
                await anova.disconnect()

    async def _serve(self, anova: PyAnova, disconnected: asyncio.Event) -> None:
        loop = asyncio.get_running_loop()
        next_poll = loop.time() + self.cfg.poll_interval
        while not self._stop.is_set():
            if disconnected.is_set() or not anova.is_connected():
                raise DeviceDisconnected("BLE link lost")
            timeout = max(0.0, min(next_poll - loop.time(), 1.0))
            try:
                suffix, payload = await asyncio.wait_for(
                    self._commands.get(), timeout=timeout
                )
            except TimeoutError:
                if loop.time() >= next_poll:
                    await self._refresh(anova)
                    next_poll = loop.time() + self.cfg.poll_interval
                continue
            await self._handle_command(anova, suffix, payload)
            await self._refresh(anova)
            next_poll = loop.time() + self.cfg.poll_interval

    def _drop_stale_commands(self) -> None:
        """Commands that arrived while the cooker was unreachable are not replayed.

        Executing a 'start' hours after it was sent would be surprising and unsafe.
        """
        dropped = 0
        while not self._commands.empty():
            self._commands.get_nowait()
            dropped += 1
        if dropped:
            logger.warning(
                "Dropped %d command(s) received while the cooker was offline", dropped
            )

    async def _handle_command(self, anova: PyAnova, suffix: str, payload: str) -> None:
        try:
            cmd = parse_command(suffix, payload, self._state.get("unit"))
        except ValueError as exc:
            logger.warning("Rejected %s=%r: %s", self.t(f"set/{suffix}"), payload, exc)
            return
        logger.info("Command %s=%r", cmd.name, cmd.value)
        if cmd.name == "power":
            await (anova.start() if cmd.value else anova.stop())
        elif cmd.name == "target_temperature":
            await anova.set_target_temperature(cmd.value)
        elif cmd.name == "timer":
            await anova.set_timer(cmd.value)
        elif cmd.name == "unit":
            await anova.set_unit(cmd.value)

    async def _refresh(self, anova: PyAnova) -> None:
        sensors = await anova.get_sensor_values()
        target = await anova.get_target_temperature()
        timer = await anova.get_timer()
        unit = await anova.get_unit()
        self._state = build_state(sensors, target, timer, unit)
        self._publish_json("state", self._state)
        logger.debug("State: %s", self._state)
        self._publish_discovery()  # no-op unless units changed

    async def _read_info(self, anova: PyAnova) -> None:
        info: dict[str, Any] = {"address": self._address}
        try:
            fw = await anova.send_read_command(ReadCommands.GetFirmwareInfo)
            info.update(
                firmware_version=fw.tagId,
                firmware_commit=fw.commitId,
                firmware_date=fw.dateCode,
            )
        except Exception as exc:  # noqa: BLE001 - info is best-effort; never fail the session over it
            logger.warning("Could not read firmware info: %s", exc)
        try:
            dev = await anova.get_device_info()
            info["device_info"] = (
                dev.value
            )  # raw IntegerValue; meaning undocumented upstream
        except Exception as exc:  # noqa: BLE001 - info is best-effort; never fail the session over it
            logger.warning("Could not read device info: %s", exc)
        self._info = info
        self._publish_json("info", info)


async def _find_device(cfg: Config, anova: PyAnova):
    if cfg.device_address:
        from bleak import BleakScanner

        logger.info("Looking for %s...", cfg.device_address)
        return await BleakScanner.find_device_by_address(
            cfg.device_address, timeout=cfg.scan_timeout
        )
    logger.info("Scanning for an Anova Nano (%.0f s)...", cfg.scan_timeout)
    devices = await anova.discover(timeout_seconds=cfg.scan_timeout)
    if len(devices) > 1:
        logger.warning(
            "Found %d Anova Nanos (%s); using %s. Set DEVICE_ADDRESS to choose.",
            len(devices),
            ", ".join(d.address for d in devices),
            devices[0].address,
        )
    return devices[0] if devices else None


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )
    bridge = AnovaMQTTBridge(Config.from_env())

    async def _main() -> None:
        import signal

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            with contextlib.suppress(NotImplementedError):
                loop.add_signal_handler(sig, bridge.stop)
        await bridge.run()

    asyncio.run(_main())


if __name__ == "__main__":
    main()
