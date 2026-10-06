FROM python:3.12-slim

# Keep Python output unbuffered for better container logs
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Default runtime parameters for your Minikube NodePort MQTT broker
ENV PROD_MQTT_HOST=192.168.178.61 \
    PROD_MQTT_PORT=31883 \
    PROD_MQTT_QOS=1 \
    PROD_SOIL_SENSOR_TOPIC="zigbee2mqtt/0xa4c138e1387d3181" \
    PROD_RELAY_SET_TOPIC="zigbee2mqtt/0xa4c138a1163e556e/set" \
    PROD_RELAY_ON_PAYLOAD="{\"state_l1\":\"ON\"}" \
    PROD_RELAY_OFF_PAYLOAD="{\"state_l1\":\"OFF\"}"

WORKDIR /app

# Install Hatch package manager
RUN pip install --no-cache-dir hatch

# Copy Hatch project files
COPY plantcontrol /app/plantcontrol

WORKDIR /app/plantcontrol

# Create Hatch environment and install the package into that environment
RUN hatch env create && hatch run pip install --no-cache-dir .

# Start soil moisture automation in production profile
CMD ["hatch", "run", "plantcontrol", "prod", "automation"]
