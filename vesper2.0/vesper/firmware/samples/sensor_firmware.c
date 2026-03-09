/**
 * Sample IoT Sensor Firmware for VESPER QEMU Simulation
 * 
 * Minimal firmware using only integer math (no floating point).
 * Simulates a temperature/humidity sensor for ARM Cortex-M3 (LM3S6965).
 * 
 * Build: make
 * Run:   make run (or: qemu-system-arm -M lm3s6965evb -nographic -kernel sensor_firmware.elf)
 */

/* Minimal type definitions (no stdlib) */
typedef unsigned char uint8_t;
typedef unsigned short uint16_t;
typedef unsigned int uint32_t;
typedef int int32_t;

/* External stack pointer from linker */
extern uint32_t _estack;

/* Forward declarations */
void Reset_Handler(void);
void NMI_Handler(void);
void HardFault_Handler(void);
void Default_Handler(void);
void main(void);

/* Vector table - placed at beginning of flash */
__attribute__((section(".isr_vector")))
uint32_t *vector_table[] = {
    (uint32_t *)&_estack,
    (uint32_t *)Reset_Handler,
    (uint32_t *)NMI_Handler,
    (uint32_t *)HardFault_Handler,
    (uint32_t *)Default_Handler,
    (uint32_t *)Default_Handler,
    (uint32_t *)Default_Handler,
    0, 0, 0, 0,
    (uint32_t *)Default_Handler,
    (uint32_t *)Default_Handler,
    0,
    (uint32_t *)Default_Handler,
    (uint32_t *)Default_Handler,
};

/* UART registers for LM3S6965 */
#define UART0_BASE  0x4000C000
#define UART0_DR    (*(volatile uint32_t *)(UART0_BASE + 0x000))
#define UART0_FR    (*(volatile uint32_t *)(UART0_BASE + 0x018))
#define UART0_IBRD  (*(volatile uint32_t *)(UART0_BASE + 0x024))
#define UART0_FBRD  (*(volatile uint32_t *)(UART0_BASE + 0x028))
#define UART0_LCRH  (*(volatile uint32_t *)(UART0_BASE + 0x02C))
#define UART0_CTL   (*(volatile uint32_t *)(UART0_BASE + 0x030))

/* System control */
#define SYSCTL_RCGC1    (*(volatile uint32_t *)0x400FE104)
#define SYSCTL_RCGC2    (*(volatile uint32_t *)0x400FE108)

/* GPIO for LED */
#define GPIO_PORTF_BASE 0x40025000
#define GPIO_PORTF_DATA (*(volatile uint32_t *)(GPIO_PORTF_BASE + 0x3FC))
#define GPIO_PORTF_DIR  (*(volatile uint32_t *)(GPIO_PORTF_BASE + 0x400))
#define GPIO_PORTF_DEN  (*(volatile uint32_t *)(GPIO_PORTF_BASE + 0x51C))

/* Sensor state using fixed-point integers (value * 10) */
static int32_t temperature_x10 = 225;  /* 22.5C */
static int32_t humidity_x10 = 450;     /* 45.0% */
static uint8_t led_state = 0;
static uint8_t motion_detected = 0;
static uint8_t switch_state = 0;
static uint32_t tick_count = 0;
static uint32_t rand_seed = 12345;

static uint32_t simple_rand(void) {
    rand_seed = rand_seed * 1103515245 + 12345;
    return (rand_seed >> 16) & 0x7FFF;
}

static void uart_init(void) {
    SYSCTL_RCGC1 |= (1 << 0);
    volatile int delay = 100;
    while (delay--);
    UART0_CTL = 0;
    UART0_IBRD = 6;
    UART0_FBRD = 33;
    UART0_LCRH = (0x3 << 5);
    UART0_CTL = (1 << 0) | (1 << 8) | (1 << 9);
}

static void uart_putc(char c) {
    while (UART0_FR & (1 << 5));
    UART0_DR = c;
}

static void uart_puts(const char *s) {
    while (*s) uart_putc(*s++);
}

static void uart_put_int(int n) {
    char buf[16];
    int i = 0;
    int neg = 0;
    if (n < 0) { neg = 1; n = -n; }
    if (n == 0) { uart_putc('0'); return; }
    while (n > 0) { buf[i++] = '0' + (n % 10); n /= 10; }
    if (neg) uart_putc('-');
    while (i > 0) uart_putc(buf[--i]);
}

static void uart_put_fixed1(int32_t val) {
    if (val < 0) { uart_putc('-'); val = -val; }
    uart_put_int(val / 10);
    uart_putc('.');
    uart_putc('0' + (val % 10));
}

static int uart_getc(void) {
    if (UART0_FR & (1 << 4)) return -1;
    return UART0_DR & 0xFF;
}

static void led_init(void) {
    SYSCTL_RCGC2 |= (1 << 5);
    volatile int delay = 100;
    while (delay--);
    GPIO_PORTF_DIR |= (1 << 0);
    GPIO_PORTF_DEN |= (1 << 0);
}

static void led_set(uint8_t on) {
    led_state = on;
    if (on) GPIO_PORTF_DATA |= (1 << 0);
    else GPIO_PORTF_DATA &= ~(1 << 0);
}

static int str_eq(const char *s1, const char *s2) {
    while (*s1 && (*s1 == *s2)) { s1++; s2++; }
    return (*s1 == *s2);
}

static void update_sensors(void) {
    int r = (int)(simple_rand() % 21) - 10;
    temperature_x10 += r;
    if (temperature_x10 < 150) temperature_x10 = 150;
    if (temperature_x10 > 350) temperature_x10 = 350;
    r = (int)(simple_rand() % 11) - 5;
    humidity_x10 += r;
    if (humidity_x10 < 200) humidity_x10 = 200;
    if (humidity_x10 > 800) humidity_x10 = 800;
    if ((simple_rand() % 200) < 1) motion_detected = 1;
}

static void process_command(const char *cmd) {
    if (str_eq(cmd, "GET_TEMP")) {
        uart_puts("TEMP:"); uart_put_fixed1(temperature_x10); uart_puts("\n");
    }
    else if (str_eq(cmd, "GET_HUMIDITY")) {
        uart_puts("HUMIDITY:"); uart_put_fixed1(humidity_x10); uart_puts("\n");
    }
    else if (str_eq(cmd, "GET_ALL")) {
        uart_puts("TEMP:"); uart_put_fixed1(temperature_x10);
        uart_puts("\nHUMIDITY:"); uart_put_fixed1(humidity_x10);
        uart_puts("\nLED:"); uart_put_int(led_state);
        uart_puts("\nSWITCH:"); uart_puts(switch_state ? "on" : "off");
        uart_puts("\n");
    }
    else if (str_eq(cmd, "ON") || str_eq(cmd, "SWITCH:on") || str_eq(cmd, "SET_LED:1")) {
        led_set(1); switch_state = 1;
        uart_puts("SWITCH:on\nACK\n");
    }
    else if (str_eq(cmd, "OFF") || str_eq(cmd, "SWITCH:off") || str_eq(cmd, "SET_LED:0")) {
        led_set(0); switch_state = 0;
        uart_puts("SWITCH:off\nACK\n");
    }
    else if (str_eq(cmd, "GET_SWITCH") || str_eq(cmd, "STATE")) {
        uart_puts("SWITCH:"); uart_puts(switch_state ? "on" : "off"); uart_puts("\n");
    }
    else if (str_eq(cmd, "GET_MOTION")) {
        uart_puts("MOTION:"); uart_puts(motion_detected ? "active" : "inactive"); uart_puts("\n");
        motion_detected = 0;
    }
    else if (str_eq(cmd, "STATUS")) {
        uart_puts("STATUS:OK\n");
    }
    else if (str_eq(cmd, "IDENTIFY") || str_eq(cmd, "ID")) {
        uart_puts("DEVICE:VESPER_SENSOR_V1\nTYPE:MULTI_SENSOR\nCAPS:temperature,humidity,switch,motion\nFIRMWARE:1.0.0\n");
    }
    else if (str_eq(cmd, "REBOOT")) {
        uart_puts("ACK:REBOOT\nBOOTED\nREADY\n");
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
    led_init();
    uart_puts("BOOTED\nDEVICE:VESPER_SENSOR_V1\nREADY\n");
    
    while (1) {
        int c, timeout = 10000;
        while (timeout-- > 0) {
            c = uart_getc();
            if (c >= 0) {
                if (c == '\n' || c == '\r') {
                    cmd_buf[cmd_len] = '\0';
                    if (cmd_len > 0) process_command(cmd_buf);
                    cmd_len = 0;
                    timeout = 10000;
                } else if (cmd_len < (int)sizeof(cmd_buf) - 1) {
                    cmd_buf[cmd_len++] = (char)c;
                }
            }
        }
        tick_count++;
        if (tick_count % 100 == 0) update_sensors();
        /* Only emit motion event once, then reset flag */
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
