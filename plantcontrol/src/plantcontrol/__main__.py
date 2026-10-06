from __future__ import annotations

import argparse
import logging

from plantcontrol.mqtt_client import (
    MQTTConfig,
    SoilAutomationConfig,
    load_config_from_env,
    load_soil_automation_config_from_env,
    run_publisher,
    run_soil_moisture_automation,
    run_subscriber,
)


LOGGER = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="plantcontrol",
        description="MQTT publisher/subscriber for development and production.",
    )
    mode_parsers = parser.add_subparsers(dest="mode", required=True)

    for mode in ("dev", "prod"):
        mode_parser = mode_parsers.add_parser(mode, help=f"{mode} profile commands")
        role_parsers = mode_parser.add_subparsers(dest="role", required=True)

        subscriber = role_parsers.add_parser("subscriber", help="Run MQTT subscriber")
        add_common_args(subscriber)

        publisher = role_parsers.add_parser("publisher", help="Run MQTT publisher")
        add_common_args(publisher)
        publisher.add_argument(
            "--message",
            default="plantcontrol ping",
            help="Payload to publish",
        )
        publisher.add_argument(
            "--retain",
            action="store_true",
            help="Set retain flag on published message",
        )
        publisher.add_argument(
            "--repeat-interval",
            type=float,
            default=0.0,
            help="Publish interval in seconds (0 = publish once)",
        )

        relay = role_parsers.add_parser(
            "relay",
            help="Switch relay channel 1 ON or OFF using Zigbee2MQTT payloads",
        )
        add_common_args(relay)
        relay.add_argument("state", choices=["on", "off", "toggle"], help="Desired relay state")
        relay.add_argument(
            "--channel",
            choices=["l1", "l2"],
            default="l1",
            help="Relay endpoint channel to control (default: l1)",
        )
        relay.add_argument(
            "--relay-set-topic",
            help="Relay set topic (usually zigbee2mqtt/<friendly_name>/set)",
        )
        relay.add_argument(
            "--relay-on-payload",
            help="Payload to switch relay channel 1 ON",
        )
        relay.add_argument(
            "--relay-off-payload",
            help="Payload to switch relay channel 1 OFF",
        )

        automation = role_parsers.add_parser(
            "automation",
            help="Soil moisture automation (dry=relay ON, not dry=relay OFF)",
        )
        add_common_args(automation)
        automation.add_argument(
            "--sensor-topic",
            help="Topic of soil moisture sensor payloads",
        )
        automation.add_argument(
            "--relay-set-topic",
            help="Relay set topic (usually zigbee2mqtt/<friendly_name>/set)",
        )
        automation.add_argument(
            "--relay-on-payload",
            help="Payload to switch relay channel 1 ON",
        )
        automation.add_argument(
            "--relay-off-payload",
            help="Payload to switch relay channel 1 OFF",
        )
        automation.add_argument(
            "--channel",
            choices=["l1", "l2"],
            default="l1",
            help="Relay endpoint channel for automation (default: l1)",
        )

    return parser


def add_common_args(command_parser: argparse.ArgumentParser) -> None:
    command_parser.add_argument("--host", help="MQTT broker host")
    command_parser.add_argument("--port", type=int, help="MQTT broker port")
    command_parser.add_argument("--topic", help="MQTT topic")
    command_parser.add_argument("--client-id", help="MQTT client id")
    command_parser.add_argument("--keepalive", type=int, help="MQTT keepalive seconds")
    command_parser.add_argument("--qos", type=int, choices=[0, 1, 2], help="MQTT QoS")


def merge_config(defaults: MQTTConfig, args: argparse.Namespace) -> MQTTConfig:
    return MQTTConfig(
        host=args.host or defaults.host,
        port=args.port or defaults.port,
        topic=args.topic or defaults.topic,
        client_id=args.client_id or f"{defaults.client_id}-{args.role}",
        keepalive=args.keepalive or defaults.keepalive,
        qos=args.qos if args.qos is not None else defaults.qos,
    )


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    parser = build_parser()
    args = parser.parse_args()

    defaults = load_config_from_env(args.mode)
    config = merge_config(defaults, args)

    try:
        if args.role == "subscriber":
            run_subscriber(config)
            return

        if args.role == "publisher":
            run_publisher(
                config,
                message=args.message,
                retain=args.retain,
                repeat_interval=args.repeat_interval,
            )
            return

        if args.role == "relay":
            automation_defaults = load_soil_automation_config_from_env(args.mode)
            relay_set_topic = args.relay_set_topic or automation_defaults.relay_set_topic
            relay_on_payload = args.relay_on_payload or automation_defaults.relay_on_payload
            relay_off_payload = args.relay_off_payload or automation_defaults.relay_off_payload
            if args.channel == "l2":
                relay_on_payload = relay_on_payload.replace("state_l1", "state_l2")
                relay_off_payload = relay_off_payload.replace("state_l1", "state_l2")
            if args.state == "toggle":
                toggle_key = "state_l2" if args.channel == "l2" else "state_l1"
                relay_payload = '{"%s":"TOGGLE"}' % toggle_key
            else:
                relay_payload = relay_on_payload if args.state == "on" else relay_off_payload

            relay_config = MQTTConfig(
                host=config.host,
                port=config.port,
                topic=relay_set_topic,
                client_id=config.client_id,
                keepalive=config.keepalive,
                qos=config.qos,
            )
            run_publisher(relay_config, message=relay_payload)
            return

        if args.role == "automation":
            automation_defaults = load_soil_automation_config_from_env(args.mode)
            sensor_topic = args.sensor_topic or automation_defaults.sensor_topic
            relay_set_topic = args.relay_set_topic or automation_defaults.relay_set_topic
            relay_on_payload = args.relay_on_payload or automation_defaults.relay_on_payload
            relay_off_payload = args.relay_off_payload or automation_defaults.relay_off_payload
            if args.channel == "l2":
                relay_on_payload = relay_on_payload.replace("state_l1", "state_l2")
                relay_off_payload = relay_off_payload.replace("state_l1", "state_l2")
            run_soil_moisture_automation(
                mqtt_config=config,
                automation_config=SoilAutomationConfig(
                    sensor_topic=sensor_topic,
                    relay_set_topic=relay_set_topic,
                    relay_on_payload=relay_on_payload,
                    relay_off_payload=relay_off_payload,
                ),
            )
            return
    except OSError as exc:
        LOGGER.error(
            "Could not connect to MQTT broker at %s:%s (%s). "
            "Use --host/--port or set %s_MQTT_HOST and %s_MQTT_PORT.",
            config.host,
            config.port,
            exc,
            args.mode.upper(),
            args.mode.upper(),
        )
        raise SystemExit(2) from exc

    parser.error("Unknown command role")


if __name__ == "__main__":
    main()
