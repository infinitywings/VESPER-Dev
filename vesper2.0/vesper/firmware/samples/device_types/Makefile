# VESPER Per-Device Firmware Build System
#
# Builds firmware variants for all supported IoT device types.
# Each device type gets its own .elf and .bin for QEMU deployment.
#
# Prerequisites:
#   - ARM GCC toolchain: brew install arm-none-eabi-gcc (macOS)
#
# Build all:   make
# Build one:   make motion_sensor.bin
# Clean:       make clean

CC = arm-none-eabi-gcc
OBJCOPY = arm-none-eabi-objcopy
SIZE = arm-none-eabi-size

CPU = cortex-m3
CFLAGS = -mcpu=$(CPU) -mthumb -nostartfiles -nostdlib
CFLAGS += -Wall -Wextra -Os
CFLAGS += -ffunction-sections -fdata-sections
LDFLAGS = -T ../linker.ld -Wl,--gc-sections

# All device firmware types
DEVICES = motion_sensor temperature_sensor smart_light humidity_sensor door_sensor smart_plug

ELFS = $(addsuffix .elf, $(DEVICES))
BINS = $(addsuffix .bin, $(DEVICES))

QEMU = qemu-system-arm
QEMU_MACHINE = lm3s6965evb
QEMU_FLAGS = -machine $(QEMU_MACHINE) -cpu $(CPU) -nographic

.PHONY: all clean list

all: $(ELFS) $(BINS)
	@echo ""
	@echo "=== VESPER Device Firmware Build Complete ==="
	@echo "Built $(words $(DEVICES)) device firmware variants:"
	@for d in $(DEVICES); do echo "  ✓ $$d.bin"; done

%.elf: %.c ../linker.ld
	$(CC) $(CFLAGS) $(LDFLAGS) -o $@ $<
	$(SIZE) $@

%.bin: %.elf
	$(OBJCOPY) -O binary $< $@

clean:
	rm -f *.o *.elf *.bin

list:
	@echo "Available device types:"
	@for d in $(DEVICES); do echo "  - $$d"; done

# Run any device in QEMU
run-%: %.elf
	$(QEMU) $(QEMU_FLAGS) -kernel $<

# Run with TCP serial forwarding (for Docker/network access)
run-tcp-%: %.elf
	$(QEMU) $(QEMU_FLAGS) -kernel $< \
		-serial tcp::15000,server,nowait
