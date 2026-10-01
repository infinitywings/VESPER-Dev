/**
 * VESPER ESP32 Device Control — Implementation
 */

#include "device_control.h"

#include <string.h>
#include "esp_log.h"
#include "nvs.h"
#include "nvs_flash.h"

static const char *TAG = "dev_ctrl";

/* Default device type — overridden by NVS key "device_type"
 * or VESPER_DEVICE_TYPE environment-mapped build flag */
#ifndef VESPER_DEVICE_TYPE
#define VESPER_DEVICE_TYPE "smart_light"
#endif

static vesper_device_state_t *s_state = NULL;

void device_control_init(vesper_device_state_t *state)
{
    s_state = state;
    memset(state, 0, sizeof(*state));

    /* Try reading device type from NVS */
    nvs_handle_t nvs;
    if (nvs_open("vesper", NVS_READONLY, &nvs) == ESP_OK) {
        size_t len = sizeof(state->device_type);
        if (nvs_get_str(nvs, "device_type", state->device_type, &len) != ESP_OK) {
            strncpy(state->device_type, VESPER_DEVICE_TYPE, sizeof(state->device_type) - 1);
        }
        len = sizeof(state->device_id);
        if (nvs_get_str(nvs, "device_id", state->device_id, &len) != ESP_OK) {
            strncpy(state->device_id, "vesper-esp32-001", sizeof(state->device_id) - 1);
        }
        nvs_close(nvs);
    } else {
        strncpy(state->device_type, VESPER_DEVICE_TYPE, sizeof(state->device_type) - 1);
        strncpy(state->device_id, "vesper-esp32-001", sizeof(state->device_id) - 1);
    }

    /* Set capabilities based on device type */
    const char *dtype = state->device_type;
    if (strstr(dtype, "light")) {
        strncpy(state->capabilities, "switch,brightness,color_temp",
                sizeof(state->capabilities) - 1);
        state->brightness = 100;
        state->color_temp = 4000;
    } else if (strstr(dtype, "motion")) {
        strncpy(state->capabilities, "motion_sensor,sensitivity",
                sizeof(state->capabilities) - 1);
    } else if (strstr(dtype, "temperature")) {
        strncpy(state->capabilities, "temperature_measurement",
                sizeof(state->capabilities) - 1);
    } else if (strstr(dtype, "humidity")) {
        strncpy(state->capabilities, "humidity_measurement",
                sizeof(state->capabilities) - 1);
    } else if (strstr(dtype, "door") || strstr(dtype, "contact")) {
        strncpy(state->capabilities, "contact_sensor,battery",
                sizeof(state->capabilities) - 1);
    } else if (strstr(dtype, "plug")) {
        strncpy(state->capabilities, "switch,power_meter,energy_meter",
                sizeof(state->capabilities) - 1);
    } else {
        strncpy(state->capabilities, "switch", sizeof(state->capabilities) - 1);
    }

    ESP_LOGI(TAG, "Device initialized: type=%s id=%s caps=%s",
             state->device_type, state->device_id, state->capabilities);
}

void device_control_set_switch(bool on)
{
    if (!s_state) return;
    s_state->power_on = on;
    s_state->toggle_count++;
    ESP_LOGI(TAG, "Switch: %s (toggles=%lu)", on ? "ON" : "OFF",
             (unsigned long)s_state->toggle_count);
    /* On real hardware: gpio_set_level(GPIO_OUTPUT_RELAY, on); */
}

void device_control_set_brightness(int level)
{
    if (!s_state) return;
    if (level < 0) level = 0;
    if (level > 100) level = 100;
    s_state->brightness = (uint8_t)level;
    ESP_LOGI(TAG, "Brightness: %d%%", level);
    /* On real hardware: ledc_set_duty(LEDC_MODE, LEDC_CHANNEL, duty); */
}

void device_control_set_color_temp(int temp_k)
{
    if (!s_state) return;
    if (temp_k < 2700) temp_k = 2700;
    if (temp_k > 6500) temp_k = 6500;
    s_state->color_temp = (uint16_t)temp_k;
    ESP_LOGI(TAG, "Color temp: %dK", temp_k);
}
