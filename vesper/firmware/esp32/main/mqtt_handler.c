/**
 * VESPER ESP32 MQTT Handler — Implementation
 *
 * Uses ESP-MQTT for MQTT 3.1.1 communication with TLS support.
 * All MQTT traffic goes through the emulated WiFi router, where:
 *   - iptables NAT translates device IPs to the WAN side
 *   - tshark can capture MQTT packets on br-iot or wlan0
 *   - Network attacks (MITM, injection) operate on real TCP streams
 *
 * V6 vulnerability: No TLS certificate pinning — accepts any server cert.
 */

#include "mqtt_handler.h"

#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "mqtt_client.h"

static const char *TAG = "mqtt_hdlr";

static esp_mqtt_client_handle_t s_client = NULL;
static bool s_connected = false;
static int s_tx_count = 0;
static int s_rx_count = 0;
static char s_uri[128] = "";

/* ── MQTT event handler ───────────────────────────────────────────────────── */
static void mqtt_event_handler(void *handler_args, esp_event_base_t base,
                                int32_t event_id, void *event_data)
{
    esp_mqtt_event_handle_t event = event_data;

    switch ((esp_mqtt_event_id_t)event_id) {
    case MQTT_EVENT_CONNECTED:
        s_connected = true;
        ESP_LOGI(TAG, "MQTT connected to %s", s_uri);

        /* Subscribe to device command topic */
        esp_mqtt_client_subscribe(s_client, "vesper/+/cmd/#", 1);
        ESP_LOGI(TAG, "Subscribed to vesper/+/cmd/#");
        break;

    case MQTT_EVENT_DISCONNECTED:
        s_connected = false;
        ESP_LOGW(TAG, "MQTT disconnected");
        break;

    case MQTT_EVENT_SUBSCRIBED:
        ESP_LOGD(TAG, "MQTT subscribed (msg_id=%d)", event->msg_id);
        break;

    case MQTT_EVENT_PUBLISHED:
        s_tx_count++;
        ESP_LOGD(TAG, "MQTT published (msg_id=%d, total_tx=%d)",
                 event->msg_id, s_tx_count);
        break;

    case MQTT_EVENT_DATA:
        s_rx_count++;
        ESP_LOGI(TAG, "MQTT data: topic=%.*s payload=%.*s",
                 event->topic_len, event->topic,
                 event->data_len, event->data);
        break;

    case MQTT_EVENT_ERROR:
        ESP_LOGE(TAG, "MQTT error: type=%d", event->error_handle->error_type);
        if (event->error_handle->error_type == MQTT_ERROR_TYPE_TCP_TRANSPORT) {
            ESP_LOGE(TAG, "  TLS error=0x%x, tls_stack=0x%x",
                     event->error_handle->esp_tls_last_esp_err,
                     event->error_handle->esp_tls_stack_err);
        }
        break;

    default:
        ESP_LOGD(TAG, "MQTT event: %d", event_id);
        break;
    }
}

/* ── Public API ───────────────────────────────────────────────────────────── */

void mqtt_handler_init(void)
{
    strncpy(s_uri, VESPER_MQTT_URI, sizeof(s_uri) - 1);

    esp_mqtt_client_config_t mqtt_cfg = {
        .broker = {
            .address = {
                .uri = s_uri,
            },
            /*
             * V6: Intentional vulnerability — no certificate verification.
             * In production, this should use certificate pinning.
             * This allows MITM attacks to intercept MQTT traffic.
             */
            .verification = {
                .skip_cert_common_name_check = true,
            },
        },
        .session = {
            .keepalive = 60,
            .disable_clean_session = false,
        },
        .network = {
            .reconnect_timeout_ms = 5000,
        },
    };

    s_client = esp_mqtt_client_init(&mqtt_cfg);
    if (!s_client) {
        ESP_LOGE(TAG, "Failed to create MQTT client");
        return;
    }

    esp_mqtt_client_register_event(s_client, ESP_EVENT_ANY_ID,
                                    mqtt_event_handler, NULL);
    esp_mqtt_client_start(s_client);

    ESP_LOGI(TAG, "MQTT client started (broker=%s)", s_uri);
}

bool mqtt_handler_is_connected(void)
{
    return s_connected;
}

const char *mqtt_handler_get_uri(void)
{
    return s_uri;
}

int mqtt_handler_get_tx_count(void)
{
    return s_tx_count;
}

int mqtt_handler_get_rx_count(void)
{
    return s_rx_count;
}

int mqtt_handler_publish(const char *topic, const char *data, int qos)
{
    if (!s_client || !s_connected) return -1;
    int msg_id = esp_mqtt_client_publish(s_client, topic, data, 0, qos, 0);
    return msg_id;
}

int mqtt_handler_subscribe(const char *topic, int qos)
{
    if (!s_client || !s_connected) return -1;
    return esp_mqtt_client_subscribe(s_client, topic, qos);
}

void mqtt_handler_stop(void)
{
    if (s_client) {
        esp_mqtt_client_stop(s_client);
        esp_mqtt_client_destroy(s_client);
        s_client = NULL;
        s_connected = false;
    }
}
