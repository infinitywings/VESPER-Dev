/**
 * VESPER ESP32 MQTT Handler
 *
 * Manages MQTT+TLS connection to the SmartThings Cloud or local broker.
 * In the emulated network, MQTT traffic traverses the WiFi router's NAT,
 * making it visible to tshark/Wireshark and attackable by the security
 * framework (MITM, topic injection, credential sniffing).
 */

#ifndef MQTT_HANDLER_H
#define MQTT_HANDLER_H

#include <stdbool.h>
#include <stdint.h>

/* Default MQTT broker for local testing (VESPER's internal broker) */
#ifndef VESPER_MQTT_URI
#define VESPER_MQTT_URI  "mqtt://192.168.4.1:1883"
#endif

/**
 * Initialize the MQTT client.
 * Connects to the broker after WiFi is available.
 */
void mqtt_handler_init(void);

/**
 * Check if MQTT is connected.
 */
bool mqtt_handler_is_connected(void);

/**
 * Get the MQTT broker URI.
 */
const char *mqtt_handler_get_uri(void);

/**
 * Get count of published messages.
 */
int mqtt_handler_get_tx_count(void);

/**
 * Get count of received messages.
 */
int mqtt_handler_get_rx_count(void);

/**
 * Publish a message to the MQTT broker.
 */
int mqtt_handler_publish(const char *topic, const char *data, int qos);

/**
 * Subscribe to an MQTT topic.
 */
int mqtt_handler_subscribe(const char *topic, int qos);

/**
 * Disconnect and cleanup.
 */
void mqtt_handler_stop(void);

#endif /* MQTT_HANDLER_H */
