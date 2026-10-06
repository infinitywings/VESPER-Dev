/**
 * VESPER ESP32 WiFi Manager
 *
 * Manages WiFi STA connection to the emulated WiFi router or a real AP.
 * In QEMU: connects via mac80211_hwsim virtual radio → hostapd AP.
 * On hardware: connects to a real WiFi network.
 *
 * The WiFi credentials are provisioned via:
 *   1. SmartThings BLE EasySetup (production flow)
 *   2. NVS pre-provisioning (QEMU testing)
 *   3. Environment variable / compile-time defaults
 */

#ifndef WIFI_MANAGER_H
#define WIFI_MANAGER_H

#include <stdbool.h>
#include <stdint.h>

/* Default WiFi credentials for VESPER emulated router */
#ifndef VESPER_WIFI_SSID
#define VESPER_WIFI_SSID     "VESPER-IoT-Network"
#endif

#ifndef VESPER_WIFI_PASS
#define VESPER_WIFI_PASS     "vesper-secure-2026"
#endif

/**
 * Initialize the WiFi subsystem in STA mode.
 * Must be called before wifi_manager_connect().
 */
void wifi_manager_init(void);

/**
 * Connect to the configured WiFi network.
 * Non-blocking; use wifi_manager_wait_connected() to block.
 */
void wifi_manager_connect(void);

/**
 * Block until WiFi is connected or timeout (ms).
 * Returns true if connected, false on timeout.
 */
bool wifi_manager_wait_connected(uint32_t timeout_ms);

/**
 * Check if WiFi is currently connected.
 */
bool wifi_manager_is_connected(void);

/**
 * Get the SSID of the connected network.
 */
const char *wifi_manager_get_ssid(void);

/**
 * Get the device's IP address as a string.
 */
const char *wifi_manager_get_ip(void);

/**
 * Get the current RSSI (signal strength) in dBm.
 */
int wifi_manager_get_rssi(void);

/**
 * Disconnect from the current WiFi network.
 */
void wifi_manager_disconnect(void);

#endif /* WIFI_MANAGER_H */
