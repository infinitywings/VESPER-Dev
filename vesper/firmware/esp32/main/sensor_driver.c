/**
 * VESPER ESP32 Sensor Driver — Implementation
 *
 * Simulates realistic sensor readings with:
 *   - Diurnal temperature cycle (18-28°C)
 *   - Humidity correlated with temperature
 *   - Stochastic motion events (Poisson process)
 *   - Power consumption that varies with device state
 *
 * These simulated values are reported via MQTT to SmartThings Cloud
 * and are also readable via the serial command interface.
 */

#include "sensor_driver.h"

#include <math.h>
#include <stdlib.h>
#include "esp_log.h"
#include "esp_timer.h"

static const char *TAG = "sensor_drv";

static uint32_t s_prng_state = 12345;
static vesper_device_state_t *s_state = NULL;
static int64_t s_start_time_us = 0;

/* Simple PRNG for sensor noise (deterministic for reproducibility) */
static uint32_t prng_next(void)
{
    s_prng_state = s_prng_state * 1664525u + 1013904223u;
    return s_prng_state;
}

static float prng_float(float min, float max)
{
    return min + (float)(prng_next() % 10000) / 10000.0f * (max - min);
}

void sensor_driver_init(vesper_device_state_t *state)
{
    s_state = state;
    s_start_time_us = esp_timer_get_time();

    /* Initial readings */
    state->temperature = 22.0f;
    state->humidity = 45.0f;
    state->motion_detected = false;
    state->door_open = false;
    state->power_watts = 0.0f;
    state->energy_kwh = 0.0f;

    ESP_LOGI(TAG, "Sensor driver initialized");
}

void sensor_driver_read_all(vesper_device_state_t *state)
{
    if (!state) return;

    int64_t elapsed_us = esp_timer_get_time() - s_start_time_us;
    float elapsed_sec = (float)elapsed_us / 1e6f;

    /* Update uptime */
    state->uptime_seconds = (uint32_t)(elapsed_sec);
    state->tick_count++;

    /* ── Temperature: diurnal cycle + noise ───────────────────────── */
    /* Simulate 24-hour cycle compressed to ~7 minutes for testing */
    float hour_angle = (elapsed_sec / 420.0f) * 2.0f * M_PI;
    float base_temp = 22.0f + 5.0f * sinf(hour_angle - M_PI / 2.0f);
    state->temperature = base_temp + prng_float(-0.3f, 0.3f);

    /* ── Humidity: inversely correlated with temperature + noise ──── */
    float base_humidity = 60.0f - (state->temperature - 18.0f) * 2.0f;
    state->humidity = base_humidity + prng_float(-2.0f, 2.0f);
    if (state->humidity < 20.0f) state->humidity = 20.0f;
    if (state->humidity > 90.0f) state->humidity = 90.0f;

    /* ── Motion: Poisson process with ~0.1 events/second ──────────── */
    bool prev_motion = state->motion_detected;
    if (state->motion_detected) {
        /* Motion active — cooldown after ~10 seconds */
        if (prng_next() % 100 < 10) {
            state->motion_detected = false;
        }
    } else {
        /* No motion — trigger with ~10% probability per read */
        if (prng_next() % 100 < 10) {
            state->motion_detected = true;
            state->motion_count++;
        }
    }
    state->motion_changed = (state->motion_detected != prev_motion);

    /* ── Door sensor: occasional toggles ──────────────────────────── */
    if (prng_next() % 100 < 3) {
        state->door_open = !state->door_open;
    }

    /* ── Power meter: depends on switch state ─────────────────────── */
    if (state->power_on) {
        float base_power = (float)state->brightness * 0.6f;  /* ~60W at 100% */
        state->power_watts = base_power + prng_float(-1.0f, 1.0f);
        state->energy_kwh += state->power_watts * (5.0f / 3600000.0f);  /* 5s interval */
    } else {
        state->power_watts = 0.5f + prng_float(0.0f, 0.2f);  /* Standby power */
    }
}
