/**
 * VESPER ESP32 Firmware — Main Application
 *
 * SmartThings Direct-Connected Device running on ESP32 (QEMU or hardware).
 * Uses the official Samsung SmartThings Device SDK for C (st-device-sdk-c)
 * to connect via WiFi → MQTT+TLS → SmartThings Cloud.
 *
 * Architecture (real SmartThings Direct-Connected Device):
 *   ESP32 ──WiFi──► Home Router ──Internet──► SmartThings Cloud
 *              ↕                                     ↕
 *           MQTT+TLS                           SmartThings App
 *
 * In VESPER QEMU mode, WiFi is provided by mac80211_hwsim virtual radios
 * through an emulated WiFi router (hostapd + dnsmasq + iptables).
 *
 * Supported device profiles (selectable via NVS or build-time):
 *   - smart_light      : Switch + Brightness + ColorTemperature
 *   - motion_sensor    : MotionSensor + Sensitivity
 *   - temperature_sensor : TemperatureMeasurement
 *   - humidity_sensor  : HumiditySensor
 *   - door_sensor      : ContactSensor + Battery
 *   - smart_plug       : Switch + PowerMeter + EnergyMeter
 *
 * Intentional vulnerabilities (for security research):
 *   - V1: Buffer overflow in FW_UPDATE command (no bounds check)
 *   - V2: AUTH always accepts (hardcoded OK)
 *   - V3: DEBUG_DUMP leaks credentials without authentication
 *   - V4: PRNG seed exposed, trivially predictable
 *   - V5: FW_APPLY with no signature verification
 *   - V6: No TLS certificate pinning (downgradeable)
 *
 * Copyright (c) 2024-2026 VESPER Project. Apache-2.0.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdbool.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/event_groups.h"
#include "esp_system.h"
#include "esp_log.h"
#include "esp_event.h"
#include "nvs_flash.h"

#include "wifi_manager.h"
#include "mqtt_handler.h"
#include "device_control.h"
#include "sensor_driver.h"

/* SmartThings Device SDK */
#include "st_dev.h"

static const char *TAG = "vesper_main";

/* ── SmartThings SDK handles ──────────────────────────────────────────────── */
static IOT_CTX *iot_ctx = NULL;
static IOT_CAP_HANDLE *cap_switch_handle = NULL;
static IOT_CAP_HANDLE *cap_brightness_handle = NULL;
static IOT_CAP_HANDLE *cap_color_temp_handle = NULL;
static IOT_CAP_HANDLE *cap_motion_handle = NULL;
static IOT_CAP_HANDLE *cap_temperature_handle = NULL;
static IOT_CAP_HANDLE *cap_humidity_handle = NULL;
static IOT_CAP_HANDLE *cap_contact_handle = NULL;
static IOT_CAP_HANDLE *cap_power_handle = NULL;

/* Onboarding config embedded from JSON files */
extern const uint8_t onboarding_config_start[] asm("_binary_onboarding_config_json_start");
extern const uint8_t onboarding_config_end[] asm("_binary_onboarding_config_json_end");
extern const uint8_t device_info_start[] asm("_binary_device_info_json_start");
extern const uint8_t device_info_end[] asm("_binary_device_info_json_end");

/* ── Device state ─────────────────────────────────────────────────────────── */
static vesper_device_state_t device_state = {0};

/* ── Intentional vulnerabilities ──────────────────────────────────────────── */
static char fw_update_buf[128];           /* V1: overflow target */
static uint8_t fw_update_len = 0;
static char auth_token[64] = "";          /* V2: always-accept auth */
static uint32_t prng_seed = 54321;        /* V4: exposed seed */

/* ── SmartThings capability callbacks ─────────────────────────────────────── */

/**
 * Switch command callback — called when SmartThings Cloud sends on/off.
 */
static void cap_switch_cmd_cb(IOT_CAP_HANDLE *handle,
                               iot_cap_cmd_data_t *cmd_data, void *usr_data)
{
    const char *cmd_str = cmd_data->cmd_data[0]->string;
    ESP_LOGI(TAG, "SmartThings switch command: %s", cmd_str);

    if (strcmp(cmd_str, "on") == 0) {
        device_state.power_on = true;
        device_control_set_switch(true);
    } else {
        device_state.power_on = false;
        device_control_set_switch(false);
    }

    /* Report state back to cloud */
    IOT_EVENT *switch_evt = st_cap_create_attr(handle,
        (char *)"switch", NULL,
        device_state.power_on ? (char *)"on" : (char *)"off",
        NULL, NULL);
    st_cap_send_attr(&switch_evt, 1);
    st_cap_free_attr(switch_evt);
}

/**
 * Brightness command callback — set level 0-100.
 */
static void cap_brightness_cmd_cb(IOT_CAP_HANDLE *handle,
                                   iot_cap_cmd_data_t *cmd_data, void *usr_data)
{
    int level = cmd_data->cmd_data[0]->number;
    ESP_LOGI(TAG, "SmartThings brightness: %d%%", level);

    device_state.brightness = (uint8_t)level;
    device_control_set_brightness(level);

    IOT_EVENT *evt = st_cap_create_attr_number(handle,
        (char *)"level", device_state.brightness, (char *)"%");
    st_cap_send_attr(&evt, 1);
    st_cap_free_attr(evt);
}

/**
 * Color temperature command callback — set 2700-6500K.
 */
static void cap_color_temp_cmd_cb(IOT_CAP_HANDLE *handle,
                                   iot_cap_cmd_data_t *cmd_data, void *usr_data)
{
    int temp_k = cmd_data->cmd_data[0]->number;
    ESP_LOGI(TAG, "SmartThings color temp: %dK", temp_k);

    device_state.color_temp = (uint16_t)temp_k;
    device_control_set_color_temp(temp_k);

    IOT_EVENT *evt = st_cap_create_attr_number(handle,
        (char *)"colorTemperature", device_state.color_temp, (char *)"K");
    st_cap_send_attr(&evt, 1);
    st_cap_free_attr(evt);
}

/* ── SmartThings connection status callback ───────────────────────────────── */
static void iot_status_cb(iot_status_t status, iot_stat_lv_t stat_lv, void *usr_data)
{
    switch (status) {
    case IOT_STATUS_IDLE:
        ESP_LOGI(TAG, "ST status: IDLE (level=%d)", stat_lv);
        break;
    case IOT_STATUS_PROVISIONING:
        ESP_LOGI(TAG, "ST status: PROVISIONING");
        break;
    case IOT_STATUS_NEED_INTERACT:
        ESP_LOGI(TAG, "ST status: NEED_INTERACT (confirm ownership)");
        /* Auto-confirm for QEMU testing */
        st_conn_ownership_confirm(iot_ctx, true);
        break;
    case IOT_STATUS_CONNECTING:
        ESP_LOGI(TAG, "ST status: CONNECTING to cloud");
        break;
    default:
        ESP_LOGW(TAG, "ST status: unknown (%d)", status);
        break;
    }
}

/* ── VESPER serial command interface (backward-compatible) ────────────────── */
/**
 * Process commands from the UART/TCP serial interface.
 * This preserves the text-based protocol from the LM3S6965 firmware
 * for backward compatibility with the attack framework, while the
 * device simultaneously maintains its SmartThings MQTT+TLS connection.
 *
 * NOTE: Intentional vulnerabilities are preserved for security research.
 */
static void process_serial_command(const char *cmd)
{
    if (strcmp(cmd, "ON") == 0) {
        device_state.power_on = true;
        device_control_set_switch(true);
        printf("SWITCH:on\nACK\n");
    }
    else if (strcmp(cmd, "OFF") == 0) {
        device_state.power_on = false;
        device_control_set_switch(false);
        printf("SWITCH:off\nACK\n");
    }
    else if (strcmp(cmd, "STATUS") == 0) {
        printf("STATUS:OK\n");
    }
    else if (strcmp(cmd, "GET_SWITCH") == 0 || strcmp(cmd, "STATE") == 0) {
        printf("SWITCH:%s\n", device_state.power_on ? "on" : "off");
    }
    else if (strncmp(cmd, "SET_BRIGHTNESS:", 15) == 0) {
        int val = atoi(cmd + 15);
        if (val >= 0 && val <= 100) device_state.brightness = val;
        device_control_set_brightness(device_state.brightness);
        printf("BRIGHTNESS:%d\nACK\n", device_state.brightness);
    }
    else if (strcmp(cmd, "GET_BRIGHTNESS") == 0) {
        printf("BRIGHTNESS:%d\n", device_state.brightness);
    }
    else if (strncmp(cmd, "SET_COLOR_TEMP:", 15) == 0) {
        int val = atoi(cmd + 15);
        if (val >= 2700 && val <= 6500) device_state.color_temp = val;
        printf("COLOR_TEMP:%dK\nACK\n", device_state.color_temp);
    }
    else if (strcmp(cmd, "GET_COLOR_TEMP") == 0) {
        printf("COLOR_TEMP:%dK\n", device_state.color_temp);
    }
    else if (strcmp(cmd, "IDENTIFY") == 0 || strcmp(cmd, "ID") == 0) {
        printf("DEVICE:VESPER_ESP32_V2\nTYPE:%s\n", device_state.device_type);
        printf("CAPS:%s\nFIRMWARE:2.0.0-esp32\n", device_state.capabilities);
        printf("PLATFORM:ESP32-QEMU\nSDK:SmartThings-STDK-C\n");
        printf("WIFI:%s\nMQTT:%s\n",
               wifi_manager_is_connected() ? "connected" : "disconnected",
               mqtt_handler_is_connected() ? "connected" : "disconnected");
        printf("ID:%s\n", device_state.device_id);
    }
    else if (strncmp(cmd, "SET_ID:", 7) == 0) {
        strncpy(device_state.device_id, cmd + 7, sizeof(device_state.device_id) - 1);
        printf("ID:%s\nACK\n", device_state.device_id);
    }
    /* V2: AUTH always accepts — intentional vulnerability */
    else if (strncmp(cmd, "AUTH:", 5) == 0) {
        strncpy(auth_token, cmd + 5, sizeof(auth_token) - 1);
        printf("AUTH:OK\nACK\n");
    }
    /* V1: Buffer overflow — no bounds check on FW_UPDATE */
    else if (strncmp(cmd, "FW_UPDATE:", 10) == 0) {
        const char *payload = cmd + 10;
        fw_update_len = 0;
        /* INTENTIONAL OVERFLOW: no check against sizeof(fw_update_buf) */
        while (*payload) {
            fw_update_buf[fw_update_len++] = *payload++;
        }
        fw_update_buf[fw_update_len] = '\0';
        printf("FW_UPDATE:ACCEPTED:%d\nACK\n", fw_update_len);
    }
    /* V5: FW_APPLY with no signature verification */
    else if (strcmp(cmd, "FW_APPLY") == 0) {
        printf("FW_APPLY:INSTALLING\nACK:REBOOT\nBOOTED\nREADY\n");
    }
    /* V3: DEBUG_DUMP leaks credentials */
    else if (strcmp(cmd, "DEBUG_DUMP") == 0) {
        printf("DEBUG:MEMORY_DUMP\n");
        printf("TOKEN:%s\n", auth_token);
        printf("SEED:%u\n", prng_seed);
        printf("FW_BUF:%s\n", fw_update_buf);
        printf("WIFI_SSID:%s\n", wifi_manager_get_ssid());
        printf("MQTT_URI:%s\n", mqtt_handler_get_uri());
    }
    else if (strcmp(cmd, "GET_ALL") == 0) {
        printf("SWITCH:%s\n", device_state.power_on ? "on" : "off");
        printf("BRIGHTNESS:%d\n", device_state.brightness);
        printf("COLOR_TEMP:%dK\n", device_state.color_temp);
        printf("TEMPERATURE:%.1f\n", device_state.temperature);
        printf("HUMIDITY:%.1f\n", device_state.humidity);
        printf("MOTION:%s\n", device_state.motion_detected ? "active" : "inactive");
        printf("WIFI:%s\n", wifi_manager_is_connected() ? "connected" : "disconnected");
        printf("MQTT:%s\n", mqtt_handler_is_connected() ? "connected" : "disconnected");
    }
    else if (strcmp(cmd, "REBOOT") == 0) {
        printf("ACK:REBOOT\n");
        esp_restart();
    }
    else if (strcmp(cmd, "WIFI_STATUS") == 0) {
        printf("WIFI_SSID:%s\n", wifi_manager_get_ssid());
        printf("WIFI_IP:%s\n", wifi_manager_get_ip());
        printf("WIFI_RSSI:%d\n", wifi_manager_get_rssi());
        printf("WIFI_STATE:%s\n", wifi_manager_is_connected() ? "connected" : "disconnected");
    }
    else if (strcmp(cmd, "MQTT_STATUS") == 0) {
        printf("MQTT_URI:%s\n", mqtt_handler_get_uri());
        printf("MQTT_STATE:%s\n", mqtt_handler_is_connected() ? "connected" : "disconnected");
        printf("MQTT_MSGS_TX:%d\n", mqtt_handler_get_tx_count());
        printf("MQTT_MSGS_RX:%d\n", mqtt_handler_get_rx_count());
    }
    else if (cmd[0] != '\0') {
        printf("ERROR:UNKNOWN:%s\n", cmd);
    }
}

/* ── Serial command reader task ───────────────────────────────────────────── */
static void serial_cmd_task(void *pvParameters)
{
    char cmd_buf[256];
    int cmd_len = 0;

    while (1) {
        int c = getchar();
        if (c == EOF) {
            vTaskDelay(pdMS_TO_TICKS(10));
            continue;
        }
        if (c == '\n' || c == '\r') {
            cmd_buf[cmd_len] = '\0';
            if (cmd_len > 0) {
                process_serial_command(cmd_buf);
            }
            cmd_len = 0;
        } else if (cmd_len < (int)sizeof(cmd_buf) - 1) {
            cmd_buf[cmd_len++] = (char)c;
        }
    }
}

/* ── Sensor reporting task ────────────────────────────────────────────────── */
static void sensor_report_task(void *pvParameters)
{
    while (1) {
        vTaskDelay(pdMS_TO_TICKS(5000));  /* Report every 5 seconds */

        if (!mqtt_handler_is_connected()) continue;

        sensor_driver_read_all(&device_state);

        /* Report temperature if capability is active */
        if (cap_temperature_handle) {
            IOT_EVENT *evt = st_cap_create_attr_number(cap_temperature_handle,
                (char *)"temperature", device_state.temperature, (char *)"C");
            st_cap_send_attr(&evt, 1);
            st_cap_free_attr(evt);
        }

        /* Report humidity */
        if (cap_humidity_handle) {
            IOT_EVENT *evt = st_cap_create_attr_number(cap_humidity_handle,
                (char *)"humidity", device_state.humidity, (char *)"%");
            st_cap_send_attr(&evt, 1);
            st_cap_free_attr(evt);
        }

        /* Report motion if changed */
        if (cap_motion_handle && device_state.motion_changed) {
            IOT_EVENT *evt = st_cap_create_attr(cap_motion_handle,
                (char *)"motion", NULL,
                device_state.motion_detected ? (char *)"active" : (char *)"inactive",
                NULL, NULL);
            st_cap_send_attr(&evt, 1);
            st_cap_free_attr(evt);
            device_state.motion_changed = false;
        }
    }
}

/* ── SmartThings SDK initialization ───────────────────────────────────────── */
static void init_smartthings(void)
{
    unsigned char *onboarding_config = (unsigned char *)onboarding_config_start;
    unsigned int onboarding_config_len = onboarding_config_end - onboarding_config_start;
    unsigned char *device_info = (unsigned char *)device_info_start;
    unsigned int device_info_len = device_info_end - device_info_start;

    /* Initialize SmartThings SDK context */
    iot_ctx = st_conn_init(onboarding_config, onboarding_config_len,
                           device_info, device_info_len);
    if (!iot_ctx) {
        ESP_LOGE(TAG, "Failed to initialize SmartThings SDK");
        return;
    }

    /* Register status callback */
    st_conn_set_noti_cb(iot_ctx, iot_status_cb, NULL);

    /* Initialize capabilities based on device type */
    const char *dtype = device_state.device_type;

    if (strstr(dtype, "light") || strstr(dtype, "plug")) {
        cap_switch_handle = st_cap_handle_init(iot_ctx, "main",
            "switch", cap_switch_cmd_cb, NULL);
    }
    if (strstr(dtype, "light")) {
        cap_brightness_handle = st_cap_handle_init(iot_ctx, "main",
            "switchLevel", cap_brightness_cmd_cb, NULL);
        cap_color_temp_handle = st_cap_handle_init(iot_ctx, "main",
            "colorTemperature", cap_color_temp_cmd_cb, NULL);
    }
    if (strstr(dtype, "motion")) {
        cap_motion_handle = st_cap_handle_init(iot_ctx, "main",
            "motionSensor", NULL, NULL);
    }
    if (strstr(dtype, "temperature")) {
        cap_temperature_handle = st_cap_handle_init(iot_ctx, "main",
            "temperatureMeasurement", NULL, NULL);
    }
    if (strstr(dtype, "humidity")) {
        cap_humidity_handle = st_cap_handle_init(iot_ctx, "main",
            "relativeHumidityMeasurement", NULL, NULL);
    }
    if (strstr(dtype, "door") || strstr(dtype, "contact")) {
        cap_contact_handle = st_cap_handle_init(iot_ctx, "main",
            "contactSensor", NULL, NULL);
    }
    if (strstr(dtype, "plug")) {
        cap_power_handle = st_cap_handle_init(iot_ctx, "main",
            "powerMeter", NULL, NULL);
    }

    /* Start connection to SmartThings Cloud */
    st_conn_start(iot_ctx, iot_status_cb, IOT_STATUS_ALL, NULL, NULL);
    ESP_LOGI(TAG, "SmartThings SDK started for device type: %s", dtype);
}

/* ── Application entry point ──────────────────────────────────────────────── */
void app_main(void)
{
    ESP_LOGI(TAG, "╔══════════════════════════════════════════════════════╗");
    ESP_LOGI(TAG, "║  VESPER ESP32 Firmware v2.0                         ║");
    ESP_LOGI(TAG, "║  SmartThings Direct-Connected Device                ║");
    ESP_LOGI(TAG, "║  Platform: ESP32 (QEMU / Hardware)                  ║");
    ESP_LOGI(TAG, "╚══════════════════════════════════════════════════════╝");

    /* Initialize NVS (required by WiFi and SmartThings SDK) */
    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        ret = nvs_flash_init();
    }
    ESP_ERROR_CHECK(ret);

    /* Initialize event loop */
    ESP_ERROR_CHECK(esp_event_loop_create_default());

    /* Initialize device state from NVS or defaults */
    device_control_init(&device_state);
    sensor_driver_init(&device_state);

    /* Print boot message (backward-compatible with attack framework) */
    printf("BOOTED\nDEVICE:VESPER_ESP32_V2\nPLATFORM:ESP32\nREADY\n");

    /* Start WiFi (connects to emulated router's AP or real AP) */
    wifi_manager_init();
    wifi_manager_connect();

    /* Wait for WiFi connection before starting MQTT/SmartThings */
    ESP_LOGI(TAG, "Waiting for WiFi connection...");
    if (wifi_manager_wait_connected(30000)) {
        ESP_LOGI(TAG, "WiFi connected: SSID=%s IP=%s",
                 wifi_manager_get_ssid(), wifi_manager_get_ip());

        /* Initialize MQTT and SmartThings SDK */
        mqtt_handler_init();
        init_smartthings();
    } else {
        ESP_LOGW(TAG, "WiFi connection timeout — running in offline mode");
    }

    /* Start background tasks */
    xTaskCreate(serial_cmd_task, "serial_cmd", 4096, NULL, 5, NULL);
    xTaskCreate(sensor_report_task, "sensor_report", 4096, NULL, 3, NULL);

    ESP_LOGI(TAG, "All tasks started. Device ready.");
}
