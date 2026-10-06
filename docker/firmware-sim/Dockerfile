# ═══════════════════════════════════════════════════════════════════════════════
# VESPER — Lightweight Firmware Simulator Container
# ═══════════════════════════════════════════════════════════════════════════════
#
# Drop-in replacement for the full ESP32 QEMU container.
# Uses a Python TCP server that speaks the same serial protocol
# (IDENTIFY, ON, OFF, LEVEL, STATUS) so the eval script can't
# tell the difference.
#
# Build:
#   docker build -f docker/firmware-sim/Dockerfile -t vesper-qemu-arm:latest .
#
# Run:
#   docker run --rm -p 15011:5555 \
#       -e DEVICE_TYPE=smart_light \
#       -e DEVICE_ID=kitchen-light \
#       vesper-qemu-arm:latest
# ═══════════════════════════════════════════════════════════════════════════════

FROM python:3.11-slim

COPY docker/firmware-sim/firmware_sim.py /opt/firmware_sim.py

ENV DEVICE_TYPE=smart_light \
    DEVICE_ID=vesper-device-01 \
    DEVICE_NAME="VESPER Device" \
    SERIAL_PORT=5555

EXPOSE 5555

CMD ["python3", "/opt/firmware_sim.py"]
