/**
 * VESPER ESP32 Device Control
 *
 * Hardware abstraction for device actuators (GPIO, PWM, etc.).
 * In QEMU, these map to virtual peripherals; on real hardware,
 * they drive actual GPIO pins.
 */

#ifndef DEVICE_CONTROL_H
#define DEVICE_CONTROL_H

#include <stdbool.h>
#include <stdint.h>

/**
 * Unified device state structure — covers all device profiles.
 */
typedef struct {
    /* Identity */
    char device_id[64];
    char device_type[32];       /* "smart_light", "motion_sensor", etc. */
    char capabilities[128];     /* Comma-separated capability list */

    /* Switch state */
    bool power_on;
    uint32_t toggle_count;

    /* Light capabilities */
    uint8_t brightness;         /* 0-100% */
    uint16_t color_temp;        /* 2700-6500K */

    /* Sensor readings */
    float temperature;          /* °C */
    float humidity;             /* % RH */
    bool motion_detected;
    bool motion_changed;        /* True if motion state changed since last report */
    uint32_t motion_count;
    bool door_open;             /* Contact sensor */

    /* Smart plug */
    float power_watts;          /* Current power draw */
    float energy_kwh;           /* Cumulative energy */

    /* Connection state */
    bool wifi_connected;
    bool mqtt_connected;

    /* Timing */
    uint32_t uptime_seconds;
    uint32_t tick_count;
} vesper_device_state_t;


/**
 * Initialize device control (GPIOs, PWM, etc.).
 * Loads device type from NVS or uses compile-time default.
 */
void device_control_init(vesper_device_state_t *state);

/**
 * Set the switch state (on/off).
 */
void device_control_set_switch(bool on);

/**
 * Set brightness level (0-100).
 */
void device_control_set_brightness(int level);

/**
 * Set color temperature (2700-6500K).
 */
void device_control_set_color_temp(int temp_k);

#endif /* DEVICE_CONTROL_H */
