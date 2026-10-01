/**
 * VESPER ESP32 WiFi Manager — Implementation
 *
 * Uses ESP-IDF WiFi driver to connect to the emulated WiFi router's AP
 * (SSID: VESPER-IoT-Network) via mac80211_hwsim virtual radios.
 *
 * In QEMU mode, the open_eth virtual NIC maps to WiFi STA behavior.
 * The hostapd-based emulated router provides DHCP, DNS, NAT, and
 * WPA2-PSK authentication — identical to a real home WiFi router.
 */

#include "wifi_manager.h"

#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/event_groups.h"
#include "esp_wifi.h"
#include "esp_event.h"
#include "esp_log.h"
#include "esp_netif.h"

static const char *TAG = "wifi_mgr";

/* Event group bits */
#define WIFI_CONNECTED_BIT  BIT0
#define WIFI_FAIL_BIT       BIT1

static EventGroupHandle_t s_wifi_event_group;
static bool s_connected = false;
static char s_ip_str[16] = "0.0.0.0";
static char s_ssid[33] = "";
static int s_rssi = -100;
static int s_retry_count = 0;

#define WIFI_MAX_RETRY  10

/* ── Event handlers ───────────────────────────────────────────────────────── */

static void wifi_event_handler(void *arg, esp_event_base_t event_base,
                                int32_t event_id, void *event_data)
{
    if (event_base == WIFI_EVENT) {
        switch (event_id) {
        case WIFI_EVENT_STA_START:
            ESP_LOGI(TAG, "WiFi STA started, connecting...");
            esp_wifi_connect();
            break;

        case WIFI_EVENT_STA_CONNECTED: {
            wifi_event_sta_connected_t *evt = (wifi_event_sta_connected_t *)event_data;
            memcpy(s_ssid, evt->ssid, evt->ssid_len);
            s_ssid[evt->ssid_len] = '\0';
            ESP_LOGI(TAG, "Associated with AP: %s (ch=%d)", s_ssid, evt->channel);
            break;
        }

        case WIFI_EVENT_STA_DISCONNECTED: {
            wifi_event_sta_disconnected_t *evt = (wifi_event_sta_disconnected_t *)event_data;
            s_connected = false;
            ESP_LOGW(TAG, "Disconnected from AP (reason=%d)", evt->reason);

            if (s_retry_count < WIFI_MAX_RETRY) {
                s_retry_count++;
                ESP_LOGI(TAG, "Retrying WiFi (%d/%d)...", s_retry_count, WIFI_MAX_RETRY);
                vTaskDelay(pdMS_TO_TICKS(1000));
                esp_wifi_connect();
            } else {
                xEventGroupSetBits(s_wifi_event_group, WIFI_FAIL_BIT);
                ESP_LOGE(TAG, "WiFi connection failed after %d retries", WIFI_MAX_RETRY);
            }
            break;
        }
        default:
            break;
        }
    }
    else if (event_base == IP_EVENT && event_id == IP_EVENT_STA_GOT_IP) {
        ip_event_got_ip_t *evt = (ip_event_got_ip_t *)event_data;
        snprintf(s_ip_str, sizeof(s_ip_str), IPSTR, IP2STR(&evt->ip_info.ip));
        s_connected = true;
        s_retry_count = 0;
        ESP_LOGI(TAG, "Got IP: %s (gw=" IPSTR ", mask=" IPSTR ")",
                 s_ip_str,
                 IP2STR(&evt->ip_info.gw),
                 IP2STR(&evt->ip_info.netmask));
        xEventGroupSetBits(s_wifi_event_group, WIFI_CONNECTED_BIT);
    }
}

/* ── Public API ───────────────────────────────────────────────────────────── */

void wifi_manager_init(void)
{
    s_wifi_event_group = xEventGroupCreate();

    /* Initialize TCP/IP stack */
    ESP_ERROR_CHECK(esp_netif_init());
    esp_netif_create_default_wifi_sta();

    /* Initialize WiFi with default config */
    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));

    /* Register event handlers */
    ESP_ERROR_CHECK(esp_event_handler_instance_register(
        WIFI_EVENT, ESP_EVENT_ANY_ID, &wifi_event_handler, NULL, NULL));
    ESP_ERROR_CHECK(esp_event_handler_instance_register(
        IP_EVENT, IP_EVENT_STA_GOT_IP, &wifi_event_handler, NULL, NULL));

    /* Configure STA mode with VESPER AP credentials */
    wifi_config_t wifi_config = {
        .sta = {
            .threshold.authmode = WIFI_AUTH_WPA2_PSK,
            .sae_pwe_h2e = WPA3_SAE_PWE_BOTH,
        },
    };
    strncpy((char *)wifi_config.sta.ssid, VESPER_WIFI_SSID,
            sizeof(wifi_config.sta.ssid) - 1);
    strncpy((char *)wifi_config.sta.password, VESPER_WIFI_PASS,
            sizeof(wifi_config.sta.password) - 1);

    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wifi_config));

    ESP_LOGI(TAG, "WiFi initialized (SSID=%s)", VESPER_WIFI_SSID);
}

void wifi_manager_connect(void)
{
    ESP_ERROR_CHECK(esp_wifi_start());
    /* Connection happens in the WIFI_EVENT_STA_START handler */
}

bool wifi_manager_wait_connected(uint32_t timeout_ms)
{
    EventBits_t bits = xEventGroupWaitBits(s_wifi_event_group,
        WIFI_CONNECTED_BIT | WIFI_FAIL_BIT,
        pdFALSE, pdFALSE,
        pdMS_TO_TICKS(timeout_ms));

    return (bits & WIFI_CONNECTED_BIT) != 0;
}

bool wifi_manager_is_connected(void)
{
    return s_connected;
}

const char *wifi_manager_get_ssid(void)
{
    return s_ssid;
}

const char *wifi_manager_get_ip(void)
{
    return s_ip_str;
}

int wifi_manager_get_rssi(void)
{
    if (s_connected) {
        wifi_ap_record_t ap_info;
        if (esp_wifi_sta_get_ap_info(&ap_info) == ESP_OK) {
            s_rssi = ap_info.rssi;
        }
    }
    return s_rssi;
}

void wifi_manager_disconnect(void)
{
    esp_wifi_disconnect();
    esp_wifi_stop();
    s_connected = false;
}
