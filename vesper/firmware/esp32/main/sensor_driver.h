/**
 * VESPER ESP32 Sensor Driver
 *
 * Simulates sensor readings for QEMU (no real hardware).
 * On real ESP32 boards, replace with actual I2C/SPI/ADC drivers.
 */

#ifndef SENSOR_DRIVER_H
#define SENSOR_DRIVER_H

#include "device_control.h"

/**
 * Initialize the sensor subsystem.
 */
void sensor_driver_init(vesper_device_state_t *state);

/**
 * Read all active sensors and update state.
 */
void sensor_driver_read_all(vesper_device_state_t *state);

#endif /* SENSOR_DRIVER_H */
