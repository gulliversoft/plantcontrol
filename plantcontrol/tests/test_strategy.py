from __future__ import annotations

import json

import pytest

from plantcontrol import __main__ as cli
from plantcontrol.mqtt_client import (
    MQTTConfig,
    _extract_is_dry,
    load_soil_automation_config_from_env,
)


def test_default_topics_use_ieee_strategy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SOIL_SENSOR_TOPIC", raising=False)
    monkeypatch.delenv("RELAY_SET_TOPIC", raising=False)
    monkeypatch.delenv("DEV_SOIL_SENSOR_TOPIC", raising=False)
    monkeypatch.delenv("DEV_RELAY_SET_TOPIC", raising=False)

    cfg = load_soil_automation_config_from_env("dev")

    assert cfg.sensor_topic == "zigbee2mqtt/0xa4c138e1387d3181"
    assert cfg.relay_set_topic == "zigbee2mqtt/0xa4c138a1163e556e/set"


def test_profile_specific_topic_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEV_SOIL_SENSOR_TOPIC", "zigbee2mqtt/custom-sensor")
    monkeypatch.setenv("DEV_RELAY_SET_TOPIC", "zigbee2mqtt/custom-relay/set")

    cfg = load_soil_automation_config_from_env("dev")

    assert cfg.sensor_topic == "zigbee2mqtt/custom-sensor"
    assert cfg.relay_set_topic == "zigbee2mqtt/custom-relay/set"


def test_extract_is_dry_from_real_payload() -> None:
    payload = json.dumps({"dry": True, "soil_moisture": 11})
    assert _extract_is_dry(payload) is True

    payload = json.dumps({"dry": False, "soil_moisture": 35})
    assert _extract_is_dry(payload) is False


def test_relay_toggle_l2_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_run_publisher(
        config: MQTTConfig,
        message: str,
        retain: bool = False,
        repeat_interval: float = 0.0,
    ) -> None:
        captured["config"] = config
        captured["message"] = message

    monkeypatch.setattr(cli, "run_publisher", fake_run_publisher)
    monkeypatch.setattr(
        "sys.argv",
        [
            "plantcontrol",
            "dev",
            "relay",
            "toggle",
            "--channel",
            "l2",
            "--host",
            "192.168.178.61",
            "--port",
            "31883",
        ],
    )

    cli.main()

    config = captured["config"]
    assert isinstance(config, MQTTConfig)
    assert config.topic == "zigbee2mqtt/0xa4c138a1163e556e/set"
    assert captured["message"] == '{"state_l2":"TOGGLE"}'


def test_automation_channel_l2_payload_mapping(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_run_soil_moisture_automation(
        mqtt_config: MQTTConfig,
        automation_config,
    ) -> None:
        captured["mqtt_config"] = mqtt_config
        captured["automation_config"] = automation_config

    monkeypatch.setattr(cli, "run_soil_moisture_automation", fake_run_soil_moisture_automation)
    monkeypatch.setattr(
        "sys.argv",
        [
            "plantcontrol",
            "prod",
            "automation",
            "--channel",
            "l2",
            "--host",
            "192.168.178.61",
            "--port",
            "31883",
        ],
    )

    cli.main()

    cfg = captured["automation_config"]
    assert cfg.relay_on_payload == '{"state_l2":"ON"}'
    assert cfg.relay_off_payload == '{"state_l2":"OFF"}'
