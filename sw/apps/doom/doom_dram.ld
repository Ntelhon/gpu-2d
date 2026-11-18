/* DOOM on G2D (bare metal on Cheshire): follows Cheshire's sw/link/dram.ld (Apache-2.0) with a 16 MB program
 * region [0x8000_0000, 0x8100_0000) instead of 8 MB (DOOM's 6 MB zone + heap); the stack starts at its top, the
 * G2D pool (G2D_POOL_ADDR) follows at 0x8100_0000, the WAD is loaded at 0xA000_0000 (doom_syscalls.c). */

ENTRY(_start)

INCLUDE cheshire_addrs.ldh

MEMORY
{
  doomram (rwx) : ORIGIN = 0x80000000, LENGTH = 0x01000000
}

SECTIONS {
  /DISCARD/ : { *(.riscv.attributes) *(.comment) }

  __stack_pointer$ = ORIGIN(doomram) + LENGTH(doomram) - 8;

  .text : {
    *(.text._start)
    *(.text)
    *(.text.*)
  } > doomram

  .misc : ALIGN(16) {
    *(.rodata)
    *(.rodata.*)
    *(.data)
    *(.data.*)
    *(.srodata)
    *(.srodata.*)
    *(.sdata)
    *(.sdata.*)
  } > doomram

  __global_pointer$ = ADDR(.misc) + SIZEOF(.misc) / 2;

  .bss : ALIGN(16) {
    __bss_start = .;
    *(.bss)
    *(.bss.*)
    *(.sbss)
    *(.sbss.*)
    __bss_end = .;
  } > doomram

  .bulk : ALIGN(16) {
    *(.bulk)
    *(.bulk.*)
  } > doomram

  __axirtgrd_base_addr__ = 0x020C1ffc;
}
