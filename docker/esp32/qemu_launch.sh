#!/bin/bash
# ═══════════════════════════════════════════════════════════════════════════════
# Launch QEMU for ESP32 with networking
#
# Uses Espressif's QEMU fork with:
#   - open_eth virtual NIC (mapped to container's eth0 via SLIRP or tap)
#   - Serial port exposed via TCP for attack framework interaction
#   - NVS flash for device type configuration
# ═══════════════════════════════════════════════════════════════════════════════
set -euo pipefail

QEMU="/opt/qemu-esp32/bin/qemu-system-xtensa"
FIRMWARE="/opt/firmware/vesper-esp32.bin"
BOOTLOADER="/opt/firmware/bootloader.bin"
PARTITION_TABLE="/opt/firmware/partition-table.bin"
SERIAL_PORT="${QEMU_SERIAL_PORT:-5555}"

# Merge firmware into a single flash image (4MB)
FLASH_IMAGE="/tmp/flash.bin"
dd if=/dev/zero of="${FLASH_IMAGE}" bs=1M count=4 2>/dev/null

# Bootloader at 0x1000
dd if="${BOOTLOADER}" of="${FLASH_IMAGE}" bs=1 seek=$((0x1000)) conv=notrunc 2>/dev/null
# Partition table at 0x8000
dd if="${PARTITION_TABLE}" of="${FLASH_IMAGE}" bs=1 seek=$((0x8000)) conv=notrunc 2>/dev/null
# Application at 0x10000
dd if="${FIRMWARE}" of="${FLASH_IMAGE}" bs=1 seek=$((0x10000)) conv=notrunc 2>/dev/null

echo "Starting QEMU ESP32..."
echo "  Firmware: ${FIRMWARE}"
echo "  Serial:   tcp::${SERIAL_PORT}"

exec "${QEMU}" \
    -nographic \
    -machine esp32 \
    -drive file="${FLASH_IMAGE}",if=mtd,format=raw \
    -serial "tcp::${SERIAL_PORT},server,nowait" \
    -nic user,model=open_eth,hostfwd=tcp::${SERIAL_PORT}-:${SERIAL_PORT} \
    -no-reboot
