#!/bin/bash
# ═══════════════════════════════════════════════════════════════════════════════
# VESPER ESP32 Device Container — Entrypoint
# ═══════════════════════════════════════════════════════════════════════════════
set -euo pipefail

DEVICE_TYPE="${DEVICE_TYPE:-smart_light}"
DEVICE_ID="${DEVICE_ID:-vesper-device-01}"
MQTT_BROKER="${MQTT_BROKER:-192.168.4.1}"
SERIAL_PORT="${QEMU_SERIAL_PORT:-5555}"

echo "╔══════════════════════════════════════════════════════════════╗"
echo "║  VESPER ESP32 Device: ${DEVICE_ID}"
echo "║  Type: ${DEVICE_TYPE}"
echo "║  MQTT: ${MQTT_BROKER}:${MQTT_PORT:-1883}"
echo "║  Serial: tcp::${SERIAL_PORT}"
echo "╚══════════════════════════════════════════════════════════════╝"

# Wait for MQTT broker to be reachable
echo "Waiting for MQTT broker at ${MQTT_BROKER}..."
for i in $(seq 1 30); do
    if mosquitto_pub -h "${MQTT_BROKER}" -t "vesper/ping" -m "hello" -q 0 2>/dev/null; then
        echo "  ✓ MQTT broker reachable"
        break
    fi
    sleep 1
done

# Launch QEMU
exec /opt/qemu_launch.sh
