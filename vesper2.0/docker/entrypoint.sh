#!/bin/bash
# VESPER Virtual Device Entrypoint
# Runs QEMU with firmware and exposes serial port via TCP
# Supports per-device firmware via FIRMWARE_ELF env var

set -e

# Default firmware path; overridden by FIRMWARE_ELF env var or volume mount
FIRMWARE="${FIRMWARE_ELF:-/firmware/sensor_firmware.elf}"

# If a device-type firmware was mounted at /firmware/device.elf, prefer it
if [ -f /firmware/device.elf ] && [ "${FIRMWARE_ELF:-}" = "" ]; then
    FIRMWARE="/firmware/device.elf"
fi

echo "╔══════════════════════════════════════════╗"
echo "║  VESPER Virtual IoT Device Container     ║"
echo "╠══════════════════════════════════════════╣"
echo "║  Device ID:   ${DEVICE_ID}               "
echo "║  Device Name: ${DEVICE_NAME}              "
echo "║  Device Type: ${DEVICE_TYPE:-generic}     "
echo "║  Firmware:    ${FIRMWARE}                 "
echo "║  Machine:     ${QEMU_MACHINE}             "
echo "║  CPU:         ${QEMU_CPU}                 "
echo "║  Serial Port: ${SERIAL_PORT}              "
echo "╚══════════════════════════════════════════╝"

# Start QEMU with serial output over TCP
# -serial tcp::PORT,server=on,wait=off  makes QEMU listen on a TCP port for serial I/O
exec qemu-system-arm \
    -M "${QEMU_MACHINE}" \
    -cpu "${QEMU_CPU}" \
    -nographic \
    -kernel "${FIRMWARE}" \
    -serial "tcp::${SERIAL_PORT},server=on,wait=off"
