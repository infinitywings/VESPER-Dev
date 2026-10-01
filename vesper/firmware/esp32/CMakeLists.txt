# VESPER ESP32 Firmware — Top-level CMake
# Uses Espressif ESP-IDF build system with SmartThings Device SDK
#
# Build:
#   source $IDF_PATH/export.sh
#   idf.py set-target esp32
#   idf.py build
#
# QEMU (Espressif fork):
#   qemu-system-xtensa -M esp32 -nographic \
#       -drive file=build/flash_image.bin,if=mtd,format=raw \
#       -nic user,model=open_eth,hostfwd=tcp::1883-:1883

cmake_minimum_required(VERSION 3.16)

# Point to the SmartThings Device SDK component
set(EXTRA_COMPONENT_DIRS
    "${CMAKE_CURRENT_LIST_DIR}/components/st-device-sdk-c/src"
)

include($ENV{IDF_PATH}/tools/cmake/project.cmake)

project(vesper_esp32_firmware)
