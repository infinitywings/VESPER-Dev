/* Linker script for LM3S6965EVB (ARM Cortex-M3) */

MEMORY
{
    FLASH (rx)  : ORIGIN = 0x00000000, LENGTH = 256K
    SRAM (rwx)  : ORIGIN = 0x20000000, LENGTH = 64K
}

SECTIONS
{
    .text :
    {
        KEEP(*(.isr_vector))
        *(.text*)
        *(.rodata*)
    } > FLASH

    .data :
    {
        *(.data*)
    } > SRAM AT > FLASH

    .bss :
    {
        *(.bss*)
        *(COMMON)
    } > SRAM
}

/* Stack configuration */
_estack = ORIGIN(SRAM) + LENGTH(SRAM);
