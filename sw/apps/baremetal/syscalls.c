// SPDX-License-Identifier: Apache-2.0
/* Bare-metal runtime for the G2D tests on Cheshire (Phase 7): the G2D HAL's platform hooks and the newlib
 * syscalls (stdout / stderr to the UART, stdin from an in-memory string, heap between .bss and the stack). */
#include <errno.h>
#include <stddef.h>
#include <stdint.h>
#include <sys/stat.h>

#include "dif/uart.h"
#include "params.h"

const char *g2d_stdin;                 /* stdin contents (NUL-terminated), set by the program */
uint32_t g2d_rtc_hz = 1000000u;        /* CLINT mtime rate (Cheshire REGS.RTC_FREQ; 1 MHz on Genesys2) */

/* CLINT mtime at CLINT base + 0xBFF8 (Cheshire address map). */
void g2d_platform_delay_us(uint32_t us)
{
    volatile uint64_t *mtime = (volatile uint64_t *)((uintptr_t)&__clint_base_addr__ + 0xBFF8u);
    uint64_t end = *mtime + ((uint64_t)us * g2d_rtc_hz + 999999u) / 1000000u;
    while (*mtime < end) {}
}
/* v2.0 (C-6): the driver's timeouts in real time. */
int g2d_platform_time_us(uint64_t *us)
{
    volatile uint64_t *mtime = (volatile uint64_t *)((uintptr_t)&__clint_base_addr__ + 0xBFF8u);
    *us = *mtime * 1000000u / g2d_rtc_hz;
    return 1;
}

#if defined(G2D_HAL_IRQ) && G2D_HAL_IRQ
/* v2.0 (C-3): sleep until the G2D interrupt (PLIC source G2D_PLIC_SRC, target 0 = hart 0 M mode) or the timeout
 * (CLINT mtimecmp of hart 0). mstatus.MIE stays 0: WFI wakes on an interrupt that is pending and enabled in mie
 * whatever mstatus.MIE is (RISC-V privileged spec, WFI), so no trap handler runs; the PLIC claim / complete ends
 * the request. A level G2D interrupt that is still pending wakes the next WFI at once (the driver re-arms FENCE). */
#ifndef G2D_PLIC_SRC
#define G2D_PLIC_SRC 58u
#endif
static volatile uint32_t *plic_reg(uint32_t off)
{
    return (volatile uint32_t *)((uintptr_t)&__plic_base_addr__ + off);
}

void g2d_platform_wait_irq(uint32_t timeout_us)
{
    static int ready;
    volatile uint64_t *mtime = (volatile uint64_t *)((uintptr_t)&__clint_base_addr__ + 0xBFF8u);
    volatile uint64_t *mtimecmp = (volatile uint64_t *)((uintptr_t)&__clint_base_addr__ + 0x4000u);
    const unsigned long bits = (1ul << 11) | (1ul << 7);      /* mie.MEIE, mie.MTIE */
    uint32_t claim;
    if (!ready) {
        *plic_reg(4u * G2D_PLIC_SRC) = 1;                                              /* priority 1 */
        *plic_reg(0x2000u + 4u * (G2D_PLIC_SRC / 32u)) |= 1u << (G2D_PLIC_SRC % 32u);   /* enable, target 0 */
        *plic_reg(0x200000u) = 0;                                                      /* threshold 0 */
        ready = 1;
    }
    *mtimecmp = *mtime + ((uint64_t)timeout_us * g2d_rtc_hz + 999999u) / 1000000u;
    __asm__ volatile("csrs mie, %0" ::"r"(bits));
    __asm__ volatile("wfi");
    __asm__ volatile("csrc mie, %0" ::"r"(bits));
    *mtimecmp = ~(uint64_t)0;
    claim = *plic_reg(0x200004u);
    if (claim)
        *plic_reg(0x200004u) = claim;                                                  /* complete */
}
#endif

/* Cache hooks: the HAL defaults (`fence`) are correct on Cheshire (see sw/hal/baremetal). */

extern char __bss_end;                 /* Cheshire dram.ld */
#define HEAP_END ((char *)0x80700000u)  /* 1 MB below the stack at the end of the 8 MB program region */

int _write(int fd, const char *p, int n)
{
    int i;
    if (fd != 1 && fd != 2) {
        errno = EBADF;
        return -1;
    }
    for (i = 0; i < n; i++) {
        if (p[i] == '\n')
            uart_write_str(&__uart_base_addr__, (void *)"\r", 1);
        uart_write_str(&__uart_base_addr__, (void *)&p[i], 1);
    }
    return n;
}

int _read(int fd, char *p, int n)
{
    int k = 0;
    if (fd != 0) {
        errno = EBADF;
        return -1;
    }
    while (k < n && g2d_stdin && *g2d_stdin)
        p[k++] = *g2d_stdin++;
    return k;
}

void *_sbrk(ptrdiff_t inc)
{
    static char *brk;
    char *old;
    if (!brk)
        brk = &__bss_end;
    if (brk + inc > HEAP_END) {
        errno = ENOMEM;
        return (void *)-1;
    }
    old = brk;
    brk += inc;
    return old;
}

int _close(int fd) { (void)fd; return -1; }
int _fstat(int fd, struct stat *st) { (void)fd; st->st_mode = S_IFCHR; return 0; }
int _isatty(int fd) { (void)fd; return 1; }
int _lseek(int fd, int off, int whence) { (void)fd; (void)off; (void)whence; return 0; }
int _open(const char *name, int flags, int mode) { (void)name; (void)flags; (void)mode; errno = ENOENT; return -1; }
int _kill(int pid, int sig) { (void)pid; (void)sig; errno = EINVAL; return -1; }
int _getpid(void) { return 1; }
