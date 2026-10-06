from __future__ import annotations

import logging
import os
import json
import signal
from dataclasses import dataclass
from threading import Event

import paho.mqtt.client as mqtt


LOGGER = logging.getLogger(__name__)


@dataclass
class MQTTConfig:
    host: str
    port: int
    topic: str
    client_id: str
    keepalive: int
    qos: int


@dataclass
class SoilAutomationConfig:
    sensor_topic: str
    relay_set_topic: str
    relay_on_payload: str
    relay_off_payload: str


def _env_with_fallback(name: str, profile: str, default: str) -> str:
    profile_key = f"{profile.upper()}_{name}"
    return os.getenv(profile_key, os.getenv(name, default))


def load_config_from_env(profile: str) -> MQTTConfig:
    return MQTTConfig(
        host=_env_with_fallback("MQTT_HOST", profile, "localhost"),
        port=int(_env_with_fallback("MQTT_PORT", profile, "1883")),
        topic=_env_with_fallback("MQTT_TOPIC", profile, f"plantcontrol/{profile}/status"),
        client_id=_env_with_fallback(
            "MQTT_CLIENT_ID",
            profile,
            f"plantcontrol-{profile}-client",
        ),
        keepalive=int(_env_with_fallback("MQTT_KEEPALIVE", profile, "60")),
        qos=int(_env_with_fallback("MQTT_QOS", profile, "1")),
    )


def load_soil_automation_config_from_env(profile: str) -> SoilAutomationConfig:
    default_sensor_topic = "zigbee2mqtt/0xa4c138e1387d3181"
    default_relay_set_topic = "zigbee2mqtt/0xa4c138a1163e556e/set"
    return SoilAutomationConfig(
        sensor_topic=_env_with_fallback("SOIL_SENSOR_TOPIC", profile, default_sensor_topic),
        relay_set_topic=_env_with_fallback("RELAY_SET_TOPIC", profile, default_relay_set_topic),
        relay_on_payload=_env_with_fallback(
            "RELAY_ON_PAYLOAD",
            profile,
            '{"state_l1":"ON"}',
        ),
        relay_off_payload=_env_with_fallback(
            "RELAY_OFF_PAYLOAD",
            profile,
            '{"state_l1":"OFF"}',
        ),
    )


def build_client(config: MQTTConfig) -> mqtt.Client:
    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id=config.client_id,
        clean_session=True,
    )
    return client


def run_subscriber(config: MQTTConfig) -> None:
    client = build_client(config)

    def on_connect(
        mqtt_client: mqtt.Client,
        userdata: object,
        flags: mqtt.ConnectFlags,
        reason_code: mqtt.ReasonCode,
        properties: mqtt.Properties | None,
    ) -> None:
        if reason_code.value == 0:
            LOGGER.info("Connected to %s:%s", config.host, config.port)
            mqtt_client.subscribe(config.topic, qos=config.qos)
            LOGGER.info("Subscribed to topic '%s' (qos=%s)", config.topic, config.qos)
        else:
            LOGGER.error("Connection failed: %s", reason_code)

    def on_message(
        mqtt_client: mqtt.Client,
        userdata: object,
        message: mqtt.MQTTMessage,
    ) -> None:
        payload = message.payload.decode("utf-8", errors="replace")
        LOGGER.info("Message on %s: %s", message.topic, payload)

    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(config.host, config.port, keepalive=config.keepalive)

    def shutdown_handler(signum: int, frame: object) -> None:
        LOGGER.info("Received signal %s, shutting down", signum)
        client.disconnect()

    signal.signal(signal.SIGINT, shutdown_handler)
    signal.signal(signal.SIGTERM, shutdown_handler)

    client.loop_forever()


def run_publisher(
    config: MQTTConfig,
    message: str,
    retain: bool = False,
    repeat_interval: float = 0.0,
) -> None:
    client = build_client(config)
    connected = Event()
    stop_requested = Event()

    def on_connect(
        mqtt_client: mqtt.Client,
        userdata: object,
        flags: mqtt.ConnectFlags,
        reason_code: mqtt.ReasonCode,
        properties: mqtt.Properties | None,
    ) -> None:
        if reason_code.value == 0:
            LOGGER.info("Connected to %s:%s", config.host, config.port)
            connected.set()
        else:
            LOGGER.error("Connection failed: %s", reason_code)

    client.on_connect = on_connect
    client.connect(config.host, config.port, keepalive=config.keepalive)
    client.loop_start()

    def shutdown_handler(signum: int, frame: object) -> None:
        LOGGER.info("Received signal %s, shutting down", signum)
        stop_requested.set()

    signal.signal(signal.SIGINT, shutdown_handler)
    signal.signal(signal.SIGTERM, shutdown_handler)

    if not connected.wait(timeout=10):
        client.loop_stop()
        client.disconnect()
        raise RuntimeError("MQTT broker connection timeout")

    try:
        while not stop_requested.is_set():
            info = client.publish(
                config.topic,
                payload=message,
                qos=config.qos,
                retain=retain,
            )
            info.wait_for_publish()
            LOGGER.info(
                "Published to '%s' (qos=%s, retain=%s): %s",
                config.topic,
                config.qos,
                retain,
                message,
            )
            if repeat_interval <= 0:
                break
            if stop_requested.wait(timeout=repeat_interval):
                break
    finally:
        client.loop_stop()
        client.disconnect()


def _extract_is_dry(payload_text: str) -> bool | None:
    text = payload_text.strip().lower()
    if not text:
        return None

    try:
        data = json.loads(payload_text)
    except json.JSONDecodeError:
        if "dry" in text:
            return True
        if "wet" in text:
            return False
        return None

    if isinstance(data, dict):
        dry_value = data.get("dry")
        if isinstance(dry_value, bool):
            return dry_value

        candidate_keys = [
            "soil_state",
            "moisture_state",
            "state",
            "soil_moisture",
            "soil_moisture_state",
            "water_state",
        ]
        for key in candidate_keys:
            value = data.get(key)
            if isinstance(value, str):
                value_lower = value.strip().lower()
                if value_lower == "dry":
                    return True
                if value_lower in {"wet", "moist", "normal", "ok"}:
                    return False
    return None


def run_soil_moisture_automation(
    mqtt_config: MQTTConfig,
    automation_config: SoilAutomationConfig,
) -> None:
    client = build_client(mqtt_config)
    last_switch_state: str | None = None

    def publish_switch_state(is_dry: bool) -> None:
        nonlocal last_switch_state
        desired_state = "ON" if is_dry else "OFF"
        if desired_state == last_switch_state:
            return

        payload = (
            automation_config.relay_on_payload
            if is_dry
            else automation_config.relay_off_payload
        )
        info = client.publish(
            automation_config.relay_set_topic,
            payload=payload,
            qos=mqtt_config.qos,
            retain=False,
        )
        info.wait_for_publish()
        last_switch_state = desired_state
        LOGGER.info(
            "Relay channel 1 set to %s via %s payload=%s",
            desired_state,
            automation_config.relay_set_topic,
            payload,
        )

    def on_connect(
        mqtt_client: mqtt.Client,
        userdata: object,
        flags: mqtt.ConnectFlags,
        reason_code: mqtt.ReasonCode,
        properties: mqtt.Properties | None,
    ) -> None:
        if reason_code.value == 0:
            LOGGER.info("Connected to %s:%s", mqtt_config.host, mqtt_config.port)
            mqtt_client.subscribe(automation_config.sensor_topic, qos=mqtt_config.qos)
            LOGGER.info(
                "Watching soil moisture topic '%s' and controlling '%s'",
                automation_config.sensor_topic,
                automation_config.relay_set_topic,
            )
        else:
            LOGGER.error("Connection failed: %s", reason_code)

    def on_message(
        mqtt_client: mqtt.Client,
        userdata: object,
        message: mqtt.MQTTMessage,
    ) -> None:
        payload_text = message.payload.decode("utf-8", errors="replace")
        LOGGER.info("Soil sensor payload: %s", payload_text)
        is_dry = _extract_is_dry(payload_text)
        if is_dry is None:
            LOGGER.warning("Could not determine dry/wet state from payload")
            return
        publish_switch_state(is_dry)

    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(mqtt_config.host, mqtt_config.port, keepalive=mqtt_config.keepalive)

    def shutdown_handler(signum: int, frame: object) -> None:
        LOGGER.info("Received signal %s, shutting down", signum)
        client.disconnect()

    signal.signal(signal.SIGINT, shutdown_handler)
    signal.signal(signal.SIGTERM, shutdown_handler)
    client.loop_forever()
