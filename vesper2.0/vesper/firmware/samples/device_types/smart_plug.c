/**
 * VESPER Smart Plug Firmware
 * ARM Cortex-M3 (LM3S6965) - QEMU
 *
 * Smart plug with power metering, scheduling, overload protection.
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

static uint8_t relay_on = 0;
static uint32_t power_watts = 0;       /* Current power draw */
static uint32_t energy_wh = 0;         /* Cumulative energy Wh */
static uint32_t voltage_mv = 120000;   /* 120.000V (millivolts) */
static uint32_t current_ma = 0;        /* milliamps */
static uint32_t overload_limit_w = 1800; /* 1800W overload */
static uint8_t overload_tripped = 0;
static uint32_t toggle_count = 0;
static uint32_t tick_count = 0;
static uint32_t rand_seed = 33333;
static char device_id[32] = "smart-plug-001";
static char auth_token[64] = "";
/* Schedule slots - vulnerable: no bounds check on index */
static uint32_t schedule[4];
static uint8_t schedule_count = 0;

static uint32_t simple_rand(void) {
    rand_seed = rand_seed * 1103515245 + 12345;
    return (rand_seed >> 16) & 0x7FFF;
}

static void uart_init(void) {
    SYSCTL_RCGC1 |= (1 << 0);
    volatile int d = 100; while (d--);
    UART0_CTL = 0; UART0_IBRD = 6; UART0_FBRD = 33;
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
    while (*prefix) { if (*s++ != *prefix++) return 0; } return 1;
}
static void str_copy(char *dst, const char *src, int max) {
    int i = 0;
    while (src[i] && i < max - 1) { dst[i] = src[i]; i++; }
    dst[i] = '\0';
}
static int str_to_int(const char *s) {
    int val = 0;
    while (*s >= '0' && *s <= '9') { val = val * 10 + (*s - '0'); s++; }
    return val;
}

static void simulate_power(void) {
    if (!relay_on) { power_watts = 0; current_ma = 0; return; }
    /* Simulate random load 50-200W with noise */
    power_watts = 100 + (simple_rand() % 100);
    current_ma = (power_watts * 1000) / 120;  /* P=IV approx */
    voltage_mv = 119500 + (simple_rand() % 1000);
    energy_wh += power_watts / 3600;  /* Approximate integration */
    /* Overload check */
    if (power_watts > overload_limit_w) {
        overload_tripped = 1;
        relay_on = 0;
        uart_puts("ALERT:OVERLOAD:"); uart_put_int(power_watts); uart_puts("W\n");
    }
}

static void process_command(char *cmd) {
    if (str_eq(cmd, "ON")) {
        if (overload_tripped) {
            uart_puts("ERROR:OVERLOAD_TRIPPED\n");
        } else {
            relay_on = 1; toggle_count++;
            uart_puts("SWITCH:on\nACK\n");
        }
    }
    else if (str_eq(cmd, "OFF")) {
        relay_on = 0; toggle_count++;
        uart_puts("SWITCH:off\nACK\n");
    }
    else if (str_eq(cmd, "GET_SWITCH")) {
        uart_puts("SWITCH:"); uart_puts(relay_on ? "on" : "off"); uart_puts("\n");
    }
    else if (str_eq(cmd, "GET_POWER")) {
        uart_puts("POWER:"); uart_put_int(power_watts); uart_puts("W\n");
    }
    else if (str_eq(cmd, "GET_ENERGY")) {
        uart_puts("ENERGY:"); uart_put_int(energy_wh); uart_puts("Wh\n");
    }
    else if (str_eq(cmd, "GET_VOLTAGE")) {
        uart_puts("VOLTAGE:"); uart_put_int(voltage_mv / 1000); uart_putc('.');
        uart_put_int((voltage_mv % 1000) / 100); uart_puts("V\n");
    }
    else if (str_eq(cmd, "GET_CURRENT")) {
        uart_puts("CURRENT:"); uart_put_int(current_ma); uart_puts("mA\n");
    }
    else if (str_eq(cmd, "RESET_ENERGY")) {
        energy_wh = 0; uart_puts("ENERGY:0Wh\nACK\n");
    }
    else if (str_eq(cmd, "RESET_OVERLOAD")) {
        overload_tripped = 0; uart_puts("OVERLOAD:reset\nACK\n");
    }
    else if (str_startswith(cmd, "SET_OVERLOAD:")) {
        overload_limit_w = str_to_int(cmd + 13);
        uart_puts("OVERLOAD_LIMIT:"); uart_put_int(overload_limit_w); uart_puts("W\nACK\n");
    }
    /* Intentionally vulnerable: schedule index not bounds-checked */
    else if (str_startswith(cmd, "SET_SCHEDULE:")) {
        int idx = cmd[13] - '0';  /* No bounds check! */
        int val = str_to_int(cmd + 15);
        schedule[idx] = val;  /* Out-of-bounds write possible */
        schedule_count++;
        uart_puts("SCHEDULE:"); uart_put_int(idx); uart_puts(":"); uart_put_int(val); uart_puts("\nACK\n");
    }
    else if (str_eq(cmd, "GET_ALL")) {
        uart_puts("SWITCH:"); uart_puts(relay_on ? "on" : "off");
        uart_puts("\nPOWER:"); uart_put_int(power_watts); uart_puts("W");
        uart_puts("\nENERGY:"); uart_put_int(energy_wh); uart_puts("Wh");
        uart_puts("\nVOLTAGE:"); uart_put_int(voltage_mv / 1000); uart_puts("V");
        uart_puts("\nOVERLOAD:"); uart_puts(overload_tripped ? "yes" : "no");
        uart_puts("\n");
    }
    else if (str_eq(cmd, "STATUS")) { uart_puts("STATUS:OK\n"); }
    else if (str_eq(cmd, "IDENTIFY") || str_eq(cmd, "ID")) {
        uart_puts("DEVICE:VESPER_PLUG_V1\nTYPE:SMART_PLUG\n");
        uart_puts("CAPS:switch,power,energy,voltage\nFIRMWARE:1.0.0\n");
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
        uart_puts("SCHED_CNT:"); uart_put_int(schedule_count); uart_puts("\n");
    }
    else if (str_eq(cmd, "REBOOT")) {
        uart_puts("ACK:REBOOT\nBOOTED\nREADY\n");
        toggle_count = 0; energy_wh = 0; overload_tripped = 0;
    }
    else if (cmd[0] != '\0') {
        uart_puts("ERROR:UNKNOWN:"); uart_puts(cmd); uart_puts("\n");
    }
}

static void delay(volatile uint32_t count) { while (count--); }

void main(void) {
    char cmd_buf[64]; int cmd_len = 0;
    uart_init();
    uart_puts("BOOTED\nDEVICE:VESPER_PLUG_V1\nREADY\n");

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
        if (tick_count % 50 == 0) simulate_power();
        delay(100000);
    }
}

void Reset_Handler(void) { main(); while (1); }
void NMI_Handler(void) { while (1); }
void HardFault_Handler(void) { while (1); }
void Default_Handler(void) { while (1); }
