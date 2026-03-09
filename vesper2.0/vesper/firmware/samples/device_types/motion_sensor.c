/**
 * VESPER Motion Sensor Firmware
 * ARM Cortex-M3 (LM3S6965) - QEMU
 *
 * Dedicated motion sensor with PIR simulation.
 * Reports motion events, sensitivity control, cooldown timer.
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
static uint8_t motion_detected = 0;
static uint8_t armed = 1;
static uint8_t sensitivity = 5;  /* 1-10 */
static uint32_t cooldown_ticks = 0;
static uint32_t cooldown_period = 300;  /* ticks between events */
static uint32_t detection_count = 0;
static uint32_t tick_count = 0;
static uint32_t rand_seed = 54321;
static char device_id[32] = "motion-sensor-001";
static char auth_token[64] = "";  /* Authentication token - intentionally weak for attack testing */

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

static int uart_getc(void) {
    if (UART0_FR & (1 << 4)) return -1;
    return UART0_DR & 0xFF;
}

static int str_eq(const char *s1, const char *s2) {
    while (*s1 && (*s1 == *s2)) { s1++; s2++; }
    return (*s1 == *s2);
}

static int str_startswith(const char *s, const char *prefix) {
    while (*prefix) {
        if (*s++ != *prefix++) return 0;
    }
    return 1;
}

static void str_copy(char *dst, const char *src, int max) {
    int i = 0;
    while (src[i] && i < max - 1) { dst[i] = src[i]; i++; }
    dst[i] = '\0';
}

static void simulate_motion(void) {
    if (!armed) return;
    if (cooldown_ticks > 0) { cooldown_ticks--; return; }
    /* Detection probability scales with sensitivity */
    uint32_t threshold = 200 - (sensitivity * 18);
    if ((simple_rand() % 1000) < threshold) {
        motion_detected = 1;
        detection_count++;
        cooldown_ticks = cooldown_period;
    }
}

static void process_command(char *cmd) {
    if (str_eq(cmd, "GET_MOTION")) {
        uart_puts("MOTION:");
        uart_puts(motion_detected ? "active" : "inactive");
        uart_puts("\n");
        motion_detected = 0;
    }
    else if (str_eq(cmd, "GET_COUNT")) {
        uart_puts("COUNT:"); uart_put_int(detection_count); uart_puts("\n");
    }
    else if (str_eq(cmd, "ARM")) {
        armed = 1;
        uart_puts("ARMED:yes\nACK\n");
    }
    else if (str_eq(cmd, "DISARM")) {
        armed = 0;
        uart_puts("ARMED:no\nACK\n");
    }
    else if (str_eq(cmd, "GET_ARMED")) {
        uart_puts("ARMED:"); uart_puts(armed ? "yes" : "no"); uart_puts("\n");
    }
    else if (str_startswith(cmd, "SET_SENSITIVITY:")) {
        int val = cmd[16] - '0';
        if (val >= 1 && val <= 9) { sensitivity = val; }
        uart_puts("SENSITIVITY:"); uart_put_int(sensitivity); uart_puts("\nACK\n");
    }
    else if (str_eq(cmd, "GET_SENSITIVITY")) {
        uart_puts("SENSITIVITY:"); uart_put_int(sensitivity); uart_puts("\n");
    }
    else if (str_eq(cmd, "RESET_COUNT")) {
        detection_count = 0;
        uart_puts("COUNT:0\nACK\n");
    }
    else if (str_eq(cmd, "STATUS")) {
        uart_puts("STATUS:OK\n");
    }
    else if (str_eq(cmd, "GET_ALL")) {
        uart_puts("MOTION:"); uart_puts(motion_detected ? "active" : "inactive");
        uart_puts("\nARMED:"); uart_puts(armed ? "yes" : "no");
        uart_puts("\nSENSITIVITY:"); uart_put_int(sensitivity);
        uart_puts("\nCOUNT:"); uart_put_int(detection_count);
        uart_puts("\n");
    }
    else if (str_eq(cmd, "ON")) {
        armed = 1;
        uart_puts("SWITCH:on\nACK\n");
    }
    else if (str_eq(cmd, "OFF")) {
        armed = 0;
        uart_puts("SWITCH:off\nACK\n");
    }
    else if (str_eq(cmd, "GET_SWITCH")) {
        uart_puts("SWITCH:"); uart_puts(armed ? "on" : "off"); uart_puts("\n");
    }
    else if (str_eq(cmd, "IDENTIFY") || str_eq(cmd, "ID")) {
        uart_puts("DEVICE:VESPER_MOTION_V1\nTYPE:MOTION_SENSOR\n");
        uart_puts("CAPS:motion,armed,sensitivity\nFIRMWARE:1.0.0\n");
        uart_puts("ID:"); uart_puts(device_id); uart_puts("\n");
    }
    /* Intentionally vulnerable: SET_ID has no auth - buffer overflow possible */
    else if (str_startswith(cmd, "SET_ID:")) {
        str_copy(device_id, cmd + 7, 32);
        uart_puts("ID:"); uart_puts(device_id); uart_puts("\nACK\n");
    }
    /* Intentionally vulnerable: AUTH with hardcoded check */
    else if (str_startswith(cmd, "AUTH:")) {
        str_copy(auth_token, cmd + 5, 64);
        uart_puts("AUTH:OK\nACK\n");  /* Always accepts - vulnerability */
    }
    /* Debug backdoor - intentionally vulnerable */
    else if (str_eq(cmd, "DEBUG_DUMP")) {
        uart_puts("DEBUG:MEMORY_DUMP\n");
        uart_puts("SEED:"); uart_put_int(rand_seed); uart_puts("\n");
        uart_puts("TOKEN:"); uart_puts(auth_token); uart_puts("\n");
        uart_puts("TICKS:"); uart_put_int(tick_count); uart_puts("\n");
    }
    else if (str_eq(cmd, "REBOOT")) {
        uart_puts("ACK:REBOOT\nBOOTED\nREADY\n");
        detection_count = 0;
        cooldown_ticks = 0;
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
    uart_puts("BOOTED\nDEVICE:VESPER_MOTION_V1\nREADY\n");

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
                /* BUG: no else branch — overlong input silently truncated
                   but cmd_len stays at max, causing next write to be
                   at buf[63] forever. Exploitable for repeated-char attacks */
            }
        }
        tick_count++;
        if (tick_count % 50 == 0) simulate_motion();
        if (motion_detected) {
            uart_puts("EVENT:MOTION:active\n");
            motion_detected = 0;
        }
        delay(100000);
    }
}

void Reset_Handler(void) { main(); while (1); }
void NMI_Handler(void) { while (1); }
void HardFault_Handler(void) { while (1); }
void Default_Handler(void) { while (1); }
