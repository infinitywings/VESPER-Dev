/**
 * VESPER Temperature Sensor Firmware
 * ARM Cortex-M3 (LM3S6965) - QEMU
 *
 * Dedicated temperature sensor with realistic thermal simulation.
 * Reports temperature in Celsius/Fahrenheit, supports thresholds.
 */

typedef unsigned char uint8_t;
typedef unsigned short uint16_t;
typedef unsigned int uint32_t;
typedef int int32_t;

extern uint32_t _estack;
void Reset_Handler(void);
void NMI_Handler(void);
void HardFault_Handler(void);
void Default_Handler(void);
void main(void);

__attribute__((section(".isr_vector")))
uint32_t *vector_table[] = {
    (uint32_t *)&_estack,
    (uint32_t *)Reset_Handler,
    (uint32_t *)NMI_Handler,
    (uint32_t *)HardFault_Handler,
    (uint32_t *)Default_Handler, (uint32_t *)Default_Handler,
    (uint32_t *)Default_Handler, 0, 0, 0, 0,
    (uint32_t *)Default_Handler, (uint32_t *)Default_Handler,
    0, (uint32_t *)Default_Handler, (uint32_t *)Default_Handler,
};

#define UART0_DR    (*(volatile uint32_t *)0x4000C000)
#define UART0_FR    (*(volatile uint32_t *)0x4000C018)
#define UART0_IBRD  (*(volatile uint32_t *)0x4000C024)
#define UART0_FBRD  (*(volatile uint32_t *)0x4000C028)
#define UART0_LCRH  (*(volatile uint32_t *)0x4000C02C)
#define UART0_CTL   (*(volatile uint32_t *)0x4000C030)
#define SYSCTL_RCGC1 (*(volatile uint32_t *)0x400FE104)

/* Device state */
static int32_t temp_x10 = 220;       /* 22.0°C * 10 (fixed-point) */
static int32_t temp_target_x10 = 220;
static int32_t temp_high_thresh = 300; /* 30.0°C */
static int32_t temp_low_thresh = 150;  /* 15.0°C */
static uint8_t alert_enabled = 1;
static uint8_t unit_fahrenheit = 0;
static uint32_t sample_count = 0;
static uint32_t tick_count = 0;
static uint32_t rand_seed = 98765;
static char device_id[32] = "temp-sensor-001";
static char auth_token[64] = "";
static uint8_t calibration_offset = 0; /* Vulnerable: signed overflow */

static uint32_t simple_rand(void) {
    rand_seed = rand_seed * 1103515245 + 12345;
    return (rand_seed >> 16) & 0x7FFF;
}

static void uart_init(void) {
    SYSCTL_RCGC1 |= (1 << 0);
    volatile int d = 100; while (d--);
    UART0_CTL = 0;
    UART0_IBRD = 6; UART0_FBRD = 33;
    UART0_LCRH = (0x3 << 5);
    UART0_CTL = (1 << 0) | (1 << 8) | (1 << 9);
}

static void uart_putc(char c) { while (UART0_FR & (1 << 5)); UART0_DR = c; }
static void uart_puts(const char *s) { while (*s) uart_putc(*s++); }
static void uart_put_int(int n) {
    char buf[16]; int i = 0;
    if (n < 0) { uart_putc('-'); n = -n; }
    if (n == 0) { uart_putc('0'); return; }
    while (n > 0) { buf[i++] = '0' + (n % 10); n /= 10; }
    while (i > 0) uart_putc(buf[--i]);
}
static void uart_put_fixed(int32_t val) {
    if (val < 0) { uart_putc('-'); val = -val; }
    uart_put_int(val / 10); uart_putc('.'); uart_put_int(val % 10);
}

static int uart_getc(void) {
    if (UART0_FR & (1 << 4)) return -1;
    return UART0_DR & 0xFF;
}

static int str_eq(const char *s1, const char *s2) {
    while (*s1 && (*s1 == *s2)) { s1++; s2++; }
    return (*s1 == *s2);
}
static int str_startswith(const char *s, const char *prefix) {
    while (*prefix) { if (*s++ != *prefix++) return 0; } return 1;
}
static void str_copy(char *dst, const char *src, int max) {
    int i = 0;
    while (src[i] && i < max - 1) { dst[i] = src[i]; i++; }
    dst[i] = '\0';
}
static int str_to_int(const char *s) {
    int neg = 0, val = 0;
    if (*s == '-') { neg = 1; s++; }
    while (*s >= '0' && *s <= '9') { val = val * 10 + (*s - '0'); s++; }
    return neg ? -val : val;
}

static void simulate_temperature(void) {
    /* Drift toward target with noise */
    int32_t delta = temp_target_x10 - temp_x10;
    if (delta > 5) temp_x10 += 1;
    else if (delta < -5) temp_x10 -= 1;
    temp_x10 += (int32_t)(simple_rand() % 5) - 2;  /* ±0.2°C noise */
    temp_x10 += calibration_offset;
    sample_count++;
}

static int32_t to_fahrenheit(int32_t c_x10) {
    return (c_x10 * 9 / 5) + 320;
}

static void check_thresholds(void) {
    if (!alert_enabled) return;
    if (temp_x10 > temp_high_thresh) {
        uart_puts("ALERT:TEMP_HIGH:");
        uart_put_fixed(temp_x10); uart_puts("\n");
    } else if (temp_x10 < temp_low_thresh) {
        uart_puts("ALERT:TEMP_LOW:");
        uart_put_fixed(temp_x10); uart_puts("\n");
    }
}

static void process_command(char *cmd) {
    if (str_eq(cmd, "GET_TEMP")) {
        uart_puts("TEMP:");
        if (unit_fahrenheit) uart_put_fixed(to_fahrenheit(temp_x10));
        else uart_put_fixed(temp_x10);
        uart_puts(unit_fahrenheit ? "F" : "C");
        uart_puts("\n");
    }
    else if (str_eq(cmd, "GET_TEMP_RAW")) {
        uart_puts("TEMP_RAW:"); uart_put_int(temp_x10); uart_puts("\n");
    }
    else if (str_startswith(cmd, "SET_TARGET:")) {
        temp_target_x10 = str_to_int(cmd + 11);
        uart_puts("TARGET:"); uart_put_fixed(temp_target_x10); uart_puts("\nACK\n");
    }
    else if (str_startswith(cmd, "SET_HIGH_THRESH:")) {
        temp_high_thresh = str_to_int(cmd + 16);
        uart_puts("HIGH_THRESH:"); uart_put_fixed(temp_high_thresh); uart_puts("\nACK\n");
    }
    else if (str_startswith(cmd, "SET_LOW_THRESH:")) {
        temp_low_thresh = str_to_int(cmd + 15);
        uart_puts("LOW_THRESH:"); uart_put_fixed(temp_low_thresh); uart_puts("\nACK\n");
    }
    else if (str_eq(cmd, "SET_UNIT:F")) {
        unit_fahrenheit = 1; uart_puts("UNIT:F\nACK\n");
    }
    else if (str_eq(cmd, "SET_UNIT:C")) {
        unit_fahrenheit = 0; uart_puts("UNIT:C\nACK\n");
    }
    /* Intentionally vulnerable: calibration with no bounds check */
    else if (str_startswith(cmd, "CALIBRATE:")) {
        calibration_offset = (uint8_t)str_to_int(cmd + 10); /* Cast loses sign */
        uart_puts("CALIBRATED:"); uart_put_int(calibration_offset); uart_puts("\nACK\n");
    }
    else if (str_eq(cmd, "STATUS")) {
        uart_puts("STATUS:OK\n");
    }
    else if (str_eq(cmd, "ON") || str_eq(cmd, "OFF")) {
        alert_enabled = str_eq(cmd, "ON") ? 1 : 0;
        uart_puts("SWITCH:"); uart_puts(alert_enabled ? "on" : "off"); uart_puts("\nACK\n");
    }
    else if (str_eq(cmd, "GET_SWITCH")) {
        uart_puts("SWITCH:"); uart_puts(alert_enabled ? "on" : "off"); uart_puts("\n");
    }
    else if (str_eq(cmd, "GET_ALL")) {
        uart_puts("TEMP:"); uart_put_fixed(temp_x10); uart_puts("C\n");
        uart_puts("TARGET:"); uart_put_fixed(temp_target_x10); uart_puts("\n");
        uart_puts("SAMPLES:"); uart_put_int(sample_count); uart_puts("\n");
        uart_puts("ALERT:"); uart_puts(alert_enabled ? "on" : "off"); uart_puts("\n");
    }
    else if (str_eq(cmd, "IDENTIFY") || str_eq(cmd, "ID")) {
        uart_puts("DEVICE:VESPER_TEMP_V1\nTYPE:TEMPERATURE_SENSOR\n");
        uart_puts("CAPS:temperature,threshold,calibration\nFIRMWARE:1.0.0\n");
        uart_puts("ID:"); uart_puts(device_id); uart_puts("\n");
    }
    else if (str_startswith(cmd, "SET_ID:")) {
        str_copy(device_id, cmd + 7, 32);
        uart_puts("ID:"); uart_puts(device_id); uart_puts("\nACK\n");
    }
    else if (str_startswith(cmd, "AUTH:")) {
        str_copy(auth_token, cmd + 5, 64);
        uart_puts("AUTH:OK\nACK\n");
    }
    else if (str_eq(cmd, "DEBUG_DUMP")) {
        uart_puts("DEBUG:MEMORY_DUMP\n");
        uart_puts("SEED:"); uart_put_int(rand_seed); uart_puts("\n");
        uart_puts("TOKEN:"); uart_puts(auth_token); uart_puts("\n");
        uart_puts("CAL:"); uart_put_int(calibration_offset); uart_puts("\n");
    }
    else if (str_eq(cmd, "REBOOT")) {
        uart_puts("ACK:REBOOT\nBOOTED\nREADY\n");
        sample_count = 0; calibration_offset = 0;
    }
    else if (cmd[0] != '\0') {
        uart_puts("ERROR:UNKNOWN:"); uart_puts(cmd); uart_puts("\n");
    }
}

static void delay(volatile uint32_t count) { while (count--); }

void main(void) {
    char cmd_buf[64];
    int cmd_len = 0;
    uart_init();
    uart_puts("BOOTED\nDEVICE:VESPER_TEMP_V1\nREADY\n");

    while (1) {
        int c, timeout = 10000;
        while (timeout-- > 0) {
            c = uart_getc();
            if (c >= 0) {
                if (c == '\n' || c == '\r') {
                    cmd_buf[cmd_len] = '\0';
                    if (cmd_len > 0) process_command(cmd_buf);
                    cmd_len = 0; timeout = 10000;
                } else if (cmd_len < (int)sizeof(cmd_buf) - 1) {
                    cmd_buf[cmd_len++] = (char)c;
                }
            }
        }
        tick_count++;
        if (tick_count % 100 == 0) {
            simulate_temperature();
            check_thresholds();
        }
        delay(100000);
    }
}

void Reset_Handler(void) { main(); while (1); }
void NMI_Handler(void) { while (1); }
void HardFault_Handler(void) { while (1); }
void Default_Handler(void) { while (1); }
