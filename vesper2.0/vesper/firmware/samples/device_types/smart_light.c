/**
 * VESPER Smart Light Firmware
 * ARM Cortex-M3 (LM3S6965) - QEMU
 *
 * Smart light with dimming, color temperature, scheduling.
 * Supports ON/OFF, brightness 0-100, color temp 2700-6500K.
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
static uint8_t power_on = 0;
static uint8_t brightness = 100;     /* 0-100% */
static uint16_t color_temp = 4000;   /* 2700-6500K */
static uint32_t on_time_ticks = 0;
static uint32_t toggle_count = 0;
static uint32_t tick_count = 0;
static char device_id[32] = "smart-light-001";
static char auth_token[64] = "";
/* Firmware update buffer - intentionally vulnerable */
static char fw_update_buf[128];
static uint8_t fw_update_len = 0;

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

static void process_command(char *cmd) {
    if (str_eq(cmd, "ON")) {
        power_on = 1; toggle_count++;
        uart_puts("SWITCH:on\nACK\n");
    }
    else if (str_eq(cmd, "OFF")) {
        power_on = 0; toggle_count++;
        uart_puts("SWITCH:off\nACK\n");
    }
    else if (str_eq(cmd, "GET_SWITCH")) {
        uart_puts("SWITCH:"); uart_puts(power_on ? "on" : "off"); uart_puts("\n");
    }
    else if (str_eq(cmd, "TOGGLE")) {
        power_on = !power_on; toggle_count++;
        uart_puts("SWITCH:"); uart_puts(power_on ? "on" : "off"); uart_puts("\nACK\n");
    }
    else if (str_startswith(cmd, "SET_BRIGHTNESS:")) {
        int val = str_to_int(cmd + 15);
        if (val >= 0 && val <= 100) brightness = val;
        uart_puts("BRIGHTNESS:"); uart_put_int(brightness); uart_puts("\nACK\n");
    }
    else if (str_eq(cmd, "GET_BRIGHTNESS")) {
        uart_puts("BRIGHTNESS:"); uart_put_int(brightness); uart_puts("\n");
    }
    else if (str_startswith(cmd, "SET_COLOR_TEMP:")) {
        int val = str_to_int(cmd + 15);
        if (val >= 2700 && val <= 6500) color_temp = val;
        uart_puts("COLOR_TEMP:"); uart_put_int(color_temp); uart_puts("K\nACK\n");
    }
    else if (str_eq(cmd, "GET_COLOR_TEMP")) {
        uart_puts("COLOR_TEMP:"); uart_put_int(color_temp); uart_puts("K\n");
    }
    else if (str_eq(cmd, "GET_TOGGLES")) {
        uart_puts("TOGGLES:"); uart_put_int(toggle_count); uart_puts("\n");
    }
    else if (str_eq(cmd, "GET_ON_TIME")) {
        uart_puts("ON_TIME:"); uart_put_int(on_time_ticks); uart_puts("\n");
    }
    else if (str_eq(cmd, "STATUS")) {
        uart_puts("STATUS:OK\n");
    }
    else if (str_eq(cmd, "GET_ALL")) {
        uart_puts("SWITCH:"); uart_puts(power_on ? "on" : "off");
        uart_puts("\nBRIGHTNESS:"); uart_put_int(brightness);
        uart_puts("\nCOLOR_TEMP:"); uart_put_int(color_temp);
        uart_puts("K\nTOGGLES:"); uart_put_int(toggle_count);
        uart_puts("\nON_TIME:"); uart_put_int(on_time_ticks); uart_puts("\n");
    }
    else if (str_eq(cmd, "IDENTIFY") || str_eq(cmd, "ID")) {
        uart_puts("DEVICE:VESPER_LIGHT_V1\nTYPE:SMART_LIGHT\n");
        uart_puts("CAPS:switch,brightness,color_temp\nFIRMWARE:1.0.0\n");
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
    /* Intentionally vulnerable: firmware update with no validation */
    else if (str_startswith(cmd, "FW_UPDATE:")) {
        /* No size check — can overflow fw_update_buf */
        int i = 10;
        fw_update_len = 0;
        while (cmd[i]) {
            fw_update_buf[fw_update_len++] = cmd[i++];  /* OVERFLOW HERE */
        }
        fw_update_buf[fw_update_len] = '\0';
        uart_puts("FW_UPDATE:ACCEPTED:"); uart_put_int(fw_update_len);
        uart_puts("\nACK\n");
    }
    else if (str_eq(cmd, "FW_APPLY")) {
        uart_puts("FW_APPLY:INSTALLING\n");
        uart_puts("ACK:REBOOT\nBOOTED\nREADY\n");
    }
    else if (str_eq(cmd, "DEBUG_DUMP")) {
        uart_puts("DEBUG:MEMORY_DUMP\n");
        uart_puts("TOKEN:"); uart_puts(auth_token); uart_puts("\n");
        uart_puts("FW_BUF:"); uart_puts(fw_update_buf); uart_puts("\n");
    }
    else if (str_eq(cmd, "REBOOT")) {
        uart_puts("ACK:REBOOT\nBOOTED\nREADY\n");
        toggle_count = 0; on_time_ticks = 0;
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
    uart_puts("BOOTED\nDEVICE:VESPER_LIGHT_V1\nREADY\n");

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
        if (power_on) on_time_ticks++;
        delay(100000);
    }
}

void Reset_Handler(void) { main(); while (1); }
void NMI_Handler(void) { while (1); }
void HardFault_Handler(void) { while (1); }
void Default_Handler(void) { while (1); }
