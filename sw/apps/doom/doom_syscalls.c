/* SPDX-License-Identifier: GPL-2.0-or-later
 *
 * DOOM on G2D, bare metal on Cheshire: newlib system calls. stdout / stderr go to the UART; the heap lies between
 * .bss and 1 MB below the stack (doom_dram.ld); files: one read-only memory file, the WAD, loaded by run_doom.sh
 * (or the SoC simulation) at DOOM_WAD_ADDR as a 16-byte header ("G2DWAD\0\0", 64-bit size) followed by the data.
 * Every path ending in ".wad" opens it; other opens fail (DOOM then runs without config / savegames).
 */
#include <errno.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/time.h>
#include <sys/times.h>

#include "dif/clint.h"
#include "dif/uart.h"
#include "params.h"

#ifndef DOOM_WAD_ADDR
#define DOOM_WAD_ADDR 0xA0000000u
#endif

extern void *__uart_base_addr__;
extern char __bss_end;
extern uint32_t g2d_rtc_hz;
#define HEAP_END ((char *)0x80F00000u)   /* stack: the top 1 MB of the 16 MB program region */
#define WAD_FD 3

static const uint8_t *g_wad;
static uint64_t g_wad_size, g_wad_pos;
static int g_wad_open;

int _write(int fd, const char *p, int n)
{
    int i;
    if (fd != 1 && fd != 2) {
        errno = EBADF;
        return -1;
    }
    for (i = 0; i < n; i++) {
        if (p[i] == '\n')
            uart_write(&__uart_base_addr__, '\r');
        uart_write(&__uart_base_addr__, (uint8_t)p[i]);
    }
    return n;
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

static int is_wad(const char *name)
{
    const size_t n = strlen(name);
    return n >= 4 && (name[n - 4] == '.') && ((name[n - 3] | 32) == 'w') && ((name[n - 2] | 32) == 'a') &&
           ((name[n - 1] | 32) == 'd');
}

int _open(const char *name, int flags, int mode)
{
    const uint8_t *h = (const uint8_t *)(uintptr_t)DOOM_WAD_ADDR;
    (void)mode;
    if (!is_wad(name) || (flags & 3) != 0 /* O_RDONLY */ || memcmp(h, "G2DWAD\0\0", 8) != 0 || g_wad_open) {
        errno = ENOENT;
        return -1;
    }
    memcpy(&g_wad_size, h + 8, 8);
    g_wad = h + 16;
    g_wad_pos = 0;
    g_wad_open = 1;
    return WAD_FD;
}

int _read(int fd, char *p, int n)
{
    uint64_t k;
    if (fd != WAD_FD || !g_wad_open) {
        errno = EBADF;
        return -1;
    }
    k = g_wad_size - g_wad_pos;
    if ((uint64_t)n < k)
        k = (uint64_t)n;
    memcpy(p, g_wad + g_wad_pos, (size_t)k);
    g_wad_pos += k;
    return (int)k;
}

int _lseek(int fd, int off, int whence)
{
    int64_t pos;
    if (fd != WAD_FD || !g_wad_open) {
        errno = ESPIPE;
        return -1;
    }
    pos = whence == 0 ? off : whence == 1 ? (int64_t)g_wad_pos + off : (int64_t)g_wad_size + off;
    if (pos < 0) {
        errno = EINVAL;
        return -1;
    }
    g_wad_pos = (uint64_t)pos;
    return (int)pos;
}

int _close(int fd)
{
    if (fd == WAD_FD && g_wad_open) {
        g_wad_open = 0;
        return 0;
    }
    return fd <= 2 ? 0 : -1;
}

int _fstat(int fd, struct stat *st)
{
    memset(st, 0, sizeof *st);
    if (fd == WAD_FD && g_wad_open) {
        st->st_mode = S_IFREG;
        st->st_size = (off_t)g_wad_size;
    } else {
        st->st_mode = S_IFCHR;
    }
    return 0;
}

int _stat(const char *name, struct stat *st)
{
    (void)name;
    (void)st;
    errno = ENOENT;
    return -1;
}

int _isatty(int fd) { return fd <= 2; }
int _kill(int pid, int sig) { (void)pid; (void)sig; errno = EINVAL; return -1; }
int _getpid(void) { return 1; }
int _unlink(const char *name) { (void)name; errno = ENOENT; return -1; }
int _link(const char *a, const char *b) { (void)a; (void)b; errno = EMLINK; return -1; }
int mkdir(const char *path, mode_t mode) { (void)path; (void)mode; errno = EROFS; return -1; }

int _gettimeofday(struct timeval *tv, void *tz)
{
    const uint64_t t = clint_get_mtime();
    (void)tz;
    tv->tv_sec = (time_t)(t / g2d_rtc_hz);
    tv->tv_usec = (suseconds_t)(t % g2d_rtc_hz * 1000000u / g2d_rtc_hz);
    return 0;
}

clock_t _times(struct tms *t) { memset(t, 0, sizeof *t); return (clock_t)clint_get_mtime(); }

/* G2D HAL platform hooks (sw/hal/baremetal): CLINT delay; the cache hooks keep their defaults (fence) */
void g2d_platform_delay_us(uint32_t us)
{
    const uint64_t end = clint_get_mtime() + ((uint64_t)us * g2d_rtc_hz + 999999u) / 1000000u;
    while (clint_get_mtime() < end) {}
}
