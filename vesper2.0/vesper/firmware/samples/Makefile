# VESPER Firmware Sample - Build Instructions
#
# This Makefile builds the sample sensor firmware for QEMU emulation.
#
# Prerequisites:
#   - ARM GCC toolchain (arm-none-eabi-gcc)
#     macOS: brew install arm-none-eabi-gcc
#     Ubuntu: sudo apt install gcc-arm-none-eabi
#
# Build:
#   make
#
# Clean:
#   make clean
#
# Run in QEMU:
#   make run

# Toolchain
CC = arm-none-eabi-gcc
OBJCOPY = arm-none-eabi-objcopy
OBJDUMP = arm-none-eabi-objdump
SIZE = arm-none-eabi-size

# Target
TARGET = sensor_firmware

# Flags
CPU = cortex-m3
CFLAGS = -mcpu=$(CPU) -mthumb -nostartfiles -nostdlib
CFLAGS += -Wall -Wextra -Os
CFLAGS += -ffunction-sections -fdata-sections
LDFLAGS = -T linker.ld -Wl,--gc-sections

# Source files
SRCS = sensor_firmware.c

# Object files
OBJS = $(SRCS:.c=.o)

# QEMU
QEMU = qemu-system-arm
QEMU_MACHINE = lm3s6965evb
QEMU_FLAGS = -machine $(QEMU_MACHINE) -cpu $(CPU) -nographic

.PHONY: all clean run debug

all: $(TARGET).elf $(TARGET).bin

$(TARGET).elf: $(OBJS) linker.ld
	$(CC) $(CFLAGS) $(LDFLAGS) -o $@ $(OBJS)
	$(SIZE) $@

$(TARGET).bin: $(TARGET).elf
	$(OBJCOPY) -O binary $< $@

%.o: %.c
	$(CC) $(CFLAGS) -c -o $@ $<

clean:
	rm -f $(OBJS) $(TARGET).elf $(TARGET).bin $(TARGET).hex

# Run in QEMU
run: $(TARGET).elf
	$(QEMU) $(QEMU_FLAGS) -kernel $(TARGET).elf

# Run in QEMU with GDB server (for debugging)
debug: $(TARGET).elf
	$(QEMU) $(QEMU_FLAGS) -kernel $(TARGET).elf -S -gdb tcp::1234 &
	@echo "GDB server started on port 1234"
	@echo "Connect with: arm-none-eabi-gdb -ex 'target remote :1234' $(TARGET).elf"

# Show disassembly
disasm: $(TARGET).elf
	$(OBJDUMP) -d $< | less
