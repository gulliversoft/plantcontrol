# plantcontrol

[![PyPI - Version](https://img.shields.io/pypi/v/plantcontrol.svg)](https://pypi.org/project/plantcontrol)
[![PyPI - Python Version](https://img.shields.io/pypi/pyversions/plantcontrol.svg)](https://pypi.org/project/plantcontrol)

-----

## Table of Contents

- [About this project](#about-this-project)
- [Installation](#installation)
- [Docker Usage](#docker-usage)
- [Tests (Hatch)](#tests-hatch)
- [Terraform + Minikube Deployment](#terraform--minikube-deployment)
- [Terraform Module Integration](#terraform-module-integration)
- [License](#license)

## About this project
I built this project after a lot of trial and error with Zigbee2MQTT, MQTT routing, and real device behavior.

The original goal was simple: water plants only when the sensor says the soil is dry.
In practice, the hard part was not Python code, but getting a reliable end-to-end chain:

- soil moisture payload arrives from Zigbee2MQTT
- the relay topic is addressed correctly
- the right relay endpoint (`l1` or `l2`) is switched
- the command is visible in logs and reproducible in CLI

This repository is the result of that hands-on debugging process.
It is intentionally practical:

- local-first development with Hatch
- explicit MQTT topics and payloads
- commands for testing, background monitoring, and troubleshooting
- production path for Terraform + Minikube

If your setup behaves similarly, this project should help you move from "message sent" to "actual hardware reaction" with less guesswork.

## Installation

```console
pip install plantcontrol
```

## Docker Usage

The Docker image runs automation mode by default (`plantcontrol prod automation`).

### Do I need a separate plantcontrol image in the cluster?

Short answer: only if plantcontrol should run as a Kubernetes workload.

You do not need a separate plantcontrol image when:

- only Zigbee2MQTT and Mosquitto run in-cluster
- plantcontrol is executed locally on the host via Hatch
- automation is handled by another system and not by this project

You do need a separate plantcontrol image when:

- plantcontrol should run as its own Deployment/Pod in Kubernetes
- you want unattended 24/7 automation inside the cluster
- lifecycle/restarts should be managed by Kubernetes instead of a local shell session

Even on a single-node cluster, Kubernetes workloads are still deployed from container images.
Single-node does not remove the image requirement for in-cluster execution.

### Decision matrix

| Operating mode | Needs plantcontrol Docker image? | Recommended when | Tradeoff |
|---|---|---|---|
| Local Hatch + in-cluster Zigbee2MQTT/Mosquitto | No | Fast debugging and manual tests | Process lifecycle is tied to your shell/session |
| In-cluster plantcontrol Deployment + Zigbee2MQTT + Mosquitto | Yes | Stable 24/7 automation | Build/push/deploy flow is required |
| Only Zigbee2MQTT + Mosquitto (no plantcontrol runtime) | No | You automate elsewhere | This repository does not execute automation in cluster |

### 1. Build image

Run from repository root:

```console
cd /home/systemlord/Documents/plantcontroll
docker build -t plantcontrol:local -f Dockerfile .
```

### 2. Run with built-in defaults

Image defaults are already set for your current setup:

- MQTT host: `192.168.178.61`
- MQTT port: `31883`
- sensor topic: `zigbee2mqtt/0xa4c138e1387d3181`
- relay topic: `zigbee2mqtt/0xa4c138a1163e556e/set`

```console
docker run --rm --name plantcontrol plantcontrol:local
```

### 3. Override runtime values

Use `-e` to override any value from the image defaults:

```console
docker run --rm --name plantcontrol \
	-e PROD_MQTT_HOST=192.168.178.61 \
	-e PROD_MQTT_PORT=31883 \
	-e PROD_SOIL_SENSOR_TOPIC="zigbee2mqtt/0xa4c138e1387d3181" \
	-e PROD_RELAY_SET_TOPIC="zigbee2mqtt/0xa4c138a1163e556e/set" \
	-e PROD_RELAY_ON_PAYLOAD='{"state_l2":"ON"}' \
	-e PROD_RELAY_OFF_PAYLOAD='{"state_l2":"OFF"}' \
	plantcontrol:local
```

### 4. Logs and stop

Run detached and inspect logs:

```console
docker run -d --name plantcontrol plantcontrol:local
docker logs -f plantcontrol
docker stop plantcontrol
docker rm plantcontrol
```

### 5. Important note about payload quoting

Unlike `hatch run`, Docker does not interpret `{...}` as Hatch context fields.
So JSON payload env values can be passed normally with shell quoting as shown above.

## Tests (Hatch)

Run test suite:

```console
hatch -e test run test
```

What is covered:

- IEEE default topic strategy for sensor and relay
- profile-specific topic overrides (`DEV_*` / `PROD_*`)
- dry-state extraction from real payload structure
- relay payload mapping for `--channel l2`
- automation payload mapping for `--channel l2`

## Usage

Use the CLI with profile + role subcommands:

```console
hatch run plantcontrol dev subscriber
hatch run plantcontrol dev publisher --message "pump=on"
hatch run plantcontrol dev relay on
hatch run plantcontrol dev relay off
hatch run plantcontrol dev relay toggle
hatch run plantcontrol dev relay on --channel l2
hatch run plantcontrol dev relay off --channel l2
hatch run plantcontrol dev relay toggle --channel l2

hatch run plantcontrol prod subscriber
hatch run plantcontrol prod publisher --message "pump=off" --retain
hatch run plantcontrol prod automation
hatch run plantcontrol prod automation --channel l2
```

Optional overrides:

```console
hatch run plantcontrol prod subscriber --host 192.168.178.61 --port 31883 --topic plantcontrol/prod/status --qos 1
hatch run plantcontrol dev publisher --repeat-interval 5 --message "heartbeat"
hatch run plantcontrol prod automation --sensor-topic "zigbee2mqtt/0xa4c138e1387d3181" --relay-set-topic "zigbee2mqtt/0xa4c138a1163e556e/set"
hatch run plantcontrol dev relay on --host 192.168.178.61 --port 31883 --relay-set-topic "zigbee2mqtt/0xa4c138a1163e556e/set"
```

If you need custom JSON payloads with `hatch run`, escape braces to avoid Hatch context parsing:

```console
hatch run plantcontrol prod automation --relay-on-payload '{{"state_l1":"ON"}}' --relay-off-payload '{{"state_l1":"OFF"}}'
```

Run long-running commands in background:

```console
hatch run plantcontrol dev subscriber --host 192.168.178.61 --port 31883 --topic "zigbee2mqtt/0xa4c138a1163e556e" &
jobs -l
fg %1
```

- add `&` to start the command in background
- use `jobs -l` to list background jobs
- use `fg %1` to bring job 1 back to foreground

Danger of parallel background subscribers:

- multiple subscribers with the same `--client-id` disconnect each other (MQTT session takeover)
- this looks like random reconnect loops and can hide real device issues
- too many background subscribers add noisy logs and make troubleshooting harder

Recommendation:

- always set explicit and unique `--client-id` for each terminal/job
- stop old background jobs before starting new ones
- use one subscriber for relay topic and one for sensor topic at most during debugging

### End-to-end test: trigger + monitor (mithoeren)

Use this workflow to verify that relay commands are sent and state updates are visible on MQTT.

1. Start subscriber in background (monitor device topic):

```console
hatch run plantcontrol dev subscriber --host 192.168.178.61 --port 31883 --topic "zigbee2mqtt/0xa4c138a1163e556e" --client-id "debug-subscriber-1" > major_tom_subscriber.log 2>&1 &
```

2. Send relay command (example for channel `l2`):

```console
hatch run plantcontrol dev relay toggle --channel l2 --host 192.168.178.61 --port 31883 --relay-set-topic "zigbee2mqtt/0xa4c138a1163e556e/set"
```

3. Watch subscriber output:

```console
tail -f major_tom_subscriber.log
```

4. Stop background subscriber when done:

```console
jobs -l
kill <PID>
```

Expected:

- relay command is published to `zigbee2mqtt/0xa4c138a1163e556e/set`
- state messages are visible on `zigbee2mqtt/0xa4c138a1163e556e`
- for channel `l2`, payload uses `state_l2`

Sensor topic check (friendly name vs IEEE):

```console
hatch run plantcontrol dev subscriber --host 192.168.178.61 --port 31883 --topic "zigbee2mqtt/0xa4c138e1387d3181" --client-id "sensor-ieee-check"
```

If you want to compare with friendly name, run in a second terminal with a different client id:

```console
hatch run plantcontrol dev subscriber --host 192.168.178.61 --port 31883 --topic "zigbee2mqtt/Soil moisture sensor 1" --client-id "sensor-friendly-check"
```

### Why IEEE topic (`0xa4...`) instead of `major Tom`

This project uses the IEEE-based topic as default:

- `zigbee2mqtt/0xa4c138a1163e556e/set`

Reason:

- IEEE address is stable and unique for the physical device
- friendly name can be changed in UI and may cause topic mismatches
- no issues with spaces/special characters in shell commands
- in your UI/event traces, this device appears consistently under `0xa4c138a1163e556e`

You can still use a friendly-name topic, but only if your Zigbee2MQTT setup is verified to resolve that exact topic reliably.

Note for relay control:

- `publisher --message "pump=on"` sends plain text and usually does not switch Zigbee2MQTT relay devices
- use `relay on|off` so payload is sent as Zigbee2MQTT-compatible JSON (`{"state_l1":"ON"}` / `{"state_l1":"OFF"}`)
- use `--channel l2` when your hardware output is on endpoint 2 (`state_l2`)
- use `relay toggle` for `{"state_l1":"TOGGLE"}` (or `{"state_l2":"TOGGLE"}` with `--channel l2`)
- the automation command also supports `--channel l1|l2` (default `l1`)

### Zigbee2MQTT Soil Automation

The automation command watches the soil moisture payload and controls relay channel 1:

- state `dry`: sends relay ON payload
- any state that is not `dry`: sends relay OFF payload

Defaults are already set for your devices:

- Sensor topic: `zigbee2mqtt/0xa4c138e1387d3181`
- Relay set topic: `zigbee2mqtt/0xa4c138a1163e556e/set`
- ON payload: `{"state_l1":"ON"}`
- OFF payload: `{"state_l1":"OFF"}`

Environment variables (global or profile-specific) are supported.
Profile-specific variables take precedence:

- `MQTT_HOST`, `MQTT_PORT`, `MQTT_TOPIC`, `MQTT_CLIENT_ID`, `MQTT_KEEPALIVE`, `MQTT_QOS`
- `DEV_MQTT_HOST`, `DEV_MQTT_PORT`, `DEV_MQTT_TOPIC`, `DEV_MQTT_CLIENT_ID`, `DEV_MQTT_KEEPALIVE`, `DEV_MQTT_QOS`
- `PROD_MQTT_HOST`, `PROD_MQTT_PORT`, `PROD_MQTT_TOPIC`, `PROD_MQTT_CLIENT_ID`, `PROD_MQTT_KEEPALIVE`, `PROD_MQTT_QOS`

Automation-specific variables:

- `SOIL_SENSOR_TOPIC`, `RELAY_SET_TOPIC`, `RELAY_ON_PAYLOAD`, `RELAY_OFF_PAYLOAD`
- `DEV_SOIL_SENSOR_TOPIC`, `DEV_RELAY_SET_TOPIC`, `DEV_RELAY_ON_PAYLOAD`, `DEV_RELAY_OFF_PAYLOAD`
- `PROD_SOIL_SENSOR_TOPIC`, `PROD_RELAY_SET_TOPIC`, `PROD_RELAY_ON_PAYLOAD`, `PROD_RELAY_OFF_PAYLOAD`

## Terraform + Minikube Deployment

This section describes the typical production-like use case on a local Minikube cluster:

- The container runs `plantcontrol prod automation`
- It listens to the Zigbee2MQTT sensor topic
- If payload contains `"dry": true`, relay channel 1 is switched ON
- If payload contains `"dry": false`, relay channel 1 is switched OFF

Container defaults:

- The Docker image already contains defaults for your NodePort setup (`192.168.178.61:31883`)
- You can override all values at runtime via Kubernetes `env` / `envFrom` (ConfigMap/Secret)

### 1. Build image into Minikube Docker daemon

Run from workspace root (`/home/systemlord/Documents/plantcontroll`):

```console
eval "$(minikube docker-env)"
docker build -t plantcontrol:local -f Dockerfile .
```

### 2. Parameterize via Terraform (ConfigMap)

Use Terraform Kubernetes provider and pass all runtime values as environment variables.

```hcl
terraform {
	required_providers {
		kubernetes = {
			source  = "hashicorp/kubernetes"
			version = "~> 2.35"
		}
	}
}

provider "kubernetes" {
	config_path    = pathexpand("~/.kube/config")
	config_context = "minikube"
}

resource "kubernetes_namespace" "plantcontrol" {
	metadata {
		name = "plantcontrol"
	}
}

resource "kubernetes_config_map" "plantcontrol_env" {
	metadata {
		name      = "plantcontrol-env"
		namespace = kubernetes_namespace.plantcontrol.metadata[0].name
	}

	data = {
		PROD_MQTT_HOST        = "192.168.178.61"
		PROD_MQTT_PORT        = "31883"
		PROD_MQTT_QOS         = "1"
		PROD_SOIL_SENSOR_TOPIC = "zigbee2mqtt/0xa4c138e1387d3181"
		PROD_RELAY_SET_TOPIC   = "zigbee2mqtt/0xa4c138a1163e556e/set"
		PROD_RELAY_ON_PAYLOAD  = "{\"state_l1\":\"ON\"}"
		PROD_RELAY_OFF_PAYLOAD = "{\"state_l1\":\"OFF\"}"
	}
}

resource "kubernetes_deployment" "plantcontrol" {
	metadata {
		name      = "plantcontrol"
		namespace = kubernetes_namespace.plantcontrol.metadata[0].name
		labels = {
			app = "plantcontrol"
		}
	}

	spec {
		replicas = 1

		selector {
			match_labels = {
				app = "plantcontrol"
			}
		}

		template {
			metadata {
				labels = {
					app = "plantcontrol"
				}
			}

			spec {
				container {
					name  = "plantcontrol"
					image = "plantcontrol:local"

					image_pull_policy = "IfNotPresent"

					env_from {
						config_map_ref {
							name = kubernetes_config_map.plantcontrol_env.metadata[0].name
						}
					}
				}
			}
		}
	}
}
```

### 3. Apply and verify

```console
terraform init
terraform apply
kubectl -n plantcontrol get pods
kubectl -n plantcontrol logs -f deploy/plantcontrol
```

### 4. MQTT access via NodePort (192.168.178.61:31883)

This setup uses a NodePort for Mosquitto. Connect plantcontrol directly to host IP and NodePort.

Connectivity check:

```console
nc -vz 192.168.178.61 31883
```

Explanation:

- `192.168.178.61`: host where Minikube/node is reachable
- `31883`: MQTT NodePort mapped to Mosquitto service port `1883`

Run plantcontrol against NodePort:

```console
hatch run plantcontrol prod automation --host 192.168.178.61 --port 31883
```

Expected behavior in logs:

- incoming sensor JSON from `zigbee2mqtt/0xa4c138e1387d3181`
- relay command published to `zigbee2mqtt/0xa4c138a1163e556e/set`
- ON when `dry=true`, OFF when `dry=false`

## Terraform Module Integration

If you already have a root Terraform project that deploys Zigbee2MQTT UI and an MQTT broker pod, integrate plantcontrol as a separate module and wire it to outputs from those modules.

Typical root project layout:

- module `broker`: owns Mosquitto Deployment/Service
- module `zigbee2mqtt`: owns Zigbee2MQTT Deployment/UI Service
- module `plantcontrol`: owns automation Deployment and ENV wiring

Example root module wiring:

```hcl
module "broker" {
	source = "./modules/broker"
	# ...
}

module "zigbee2mqtt" {
	source = "./modules/zigbee2mqtt"
	# ...
}

module "plantcontrol" {
	source = "./modules/plantcontrol"

	namespace             = module.zigbee2mqtt.namespace
	mqtt_host             = module.broker.host
	mqtt_port             = module.broker.port
	soil_sensor_topic     = "zigbee2mqtt/0xa4c138e1387d3181"
	relay_set_topic       = "zigbee2mqtt/0xa4c138a1163e556e/set"
	relay_on_payload      = "{\"state_l1\":\"ON\"}"
	relay_off_payload     = "{\"state_l1\":\"OFF\"}"
	image                 = "plantcontrol:local"
}
```

Recommended module outputs from `broker` module:

- `host` (service DNS name or node address)
- `port` (service port or NodePort)
- `namespace`

Recommended module outputs from `zigbee2mqtt` module:

- `namespace`
- optional `base_topic` if centralized there

Integration notes:

- Keep broker and zigbee2mqtt pods independent from plantcontrol pod (clear ownership and troubleshooting).
- Pass MQTT endpoint and topics through module variables, not hardcoded values.
- If all modules run in the same namespace, prefer ClusterIP DNS (`mosquitto.<ns>.svc.cluster.local`) over NodePort.
- Use NodePort only when plantcontrol runs outside the cluster.

## License

`plantcontrol` is distributed under the terms of the [MIT](https://spdx.org/licenses/MIT.html) license.
