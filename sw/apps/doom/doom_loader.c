// SPDX-License-Identifier: Apache-2.0
//
// DOOM on G2D: SD-card loader (boot mode 1). Cheshire's boot ROM finds the GPT partition of the zero-stage-loader
// type on the SD card, copies it (this program, linked with Cheshire's rom.ld into the LLC scratchpad, <= 48 KB) and
// starts it, passing its SD read routine through the SoC scratch registers (Cheshire sw/lib/gpt.c: scratch[0] =
// read | 1, scratch[1] = device handle, scratch[3] = the boot ROM's global pointer). Written after Cheshire's own
// zero-stage loader sw/boot/zsl.c (Apache-2.0), which loads a device tree and a firmware image; this one loads:
//   partition "firmware" (Cheshire's FW type GUID)  -> 0x8000_0000  DOOM's program (doom.board.bin)
//   partition "DOOM WAD" (DOOM_WAD_TYPE_GUID)       -> 0xA000_0000  the WAD image (16-byte header + WAD, doom_wad.bin)
// then flushes the data cache and jumps to 0x8000_0000. Progress goes to the UART at 115200 baud.

#include <stdint.h>

#include "dif/clint.h"
#include "dif/uart.h"
#include "gpt.h"
#include "params.h"
#include "printf.h"
#include "regs/cheshire.h"
#include "util.h"

typedef int (*entry_t)(void);

// Type GUID of the WAD partition, chosen for this project: D00D0AD1-6B2D-4A5E-9F3A-5C0FFEE1D00D (fpga/scripts
// mk_doom_sd.sh writes the same string); stored as gpt.c compares GUIDs (two little-endian 64-bit words).
static const uint64_t DOOM_WAD_TYPE_GUID[2] = {0x4A5E6B2DD00D0AD1UL, 0x0DD0E1FE0F5C3A9FUL};

#define DOOM_PROG_ADDR ((void *)0x80000000UL)
#define DOOM_WAD_ADDR ((void *)0xA0000000UL)
#define CHUNK (1u << 20)   // read in 1 MB pieces to print progress

static gpt_read_t rread;
static void *rgp;

// The boot ROM's read routine runs with the boot ROM's global pointer. util.h's gprw() declares a gp clobber, which
// GCC 13 rejects ("PIC register clobbered"); gp is a fixed register the compiler never allocates, so the swap is done
// here without the clobber: the routine and the ROM gp are loaded into locals first, and this file is compiled with
// -mno-relax so that no access becomes gp-relative while the ROM's gp is installed.
static __attribute__((noinline)) int grread(void *priv, void *buf, uint64_t addr, uint64_t len)
{
    const gpt_read_t f = rread;
    void *const rom_gp = rgp;
    void *own_gp;
    int ret;
    __asm__ volatile("mv %0, gp\n\tmv gp, %1" : "=&r"(own_gp) : "r"(rom_gp) : "memory");
    ret = f(priv, buf, addr, len);
    __asm__ volatile("mv gp, %0" ::"r"(own_gp) : "memory");
    return ret;
}

static void halt(void)
{
    printf("[DOOM loader] stopped\r\n");
    while (1) wfi();
}

static uint64_t load(void *priv, const uint64_t *tguid, void *dst, const char *name)
{
    uint64_t lba_begin, lba_end, bytes, done;
    int64_t idx = -1;
    if (gpt_find_partition(grread, priv, &idx, &lba_begin, &lba_end, 0x200000, tguid, 0) || idx < 0) {
        printf("[DOOM loader] no %s partition (type GUID 0x%llx%llx)\r\n", name, tguid[1], tguid[0]);
        halt();
    }
    bytes = 0x200 * (lba_end - lba_begin + 1);
    printf("[DOOM loader] %s: partition %d, LBA %d-%d, %d KB -> 0x%lx\r\n", name, (int)idx, (int)lba_begin,
           (int)lba_end, (int)(bytes >> 10), dst);
    for (done = 0; done < bytes; done += CHUNK) {
        const uint64_t n = (bytes - done < CHUNK) ? bytes - done : CHUNK;
        if (grread(priv, (uint8_t *)dst + done, 0x200 * lba_begin + done, n)) {
            printf("\r\n[DOOM loader] read error at %d KB of %s\r\n", (int)(done >> 10), name);
            halt();
        }
        if (bytes > 4 * CHUNK) printf("\r[DOOM loader]   %d / %d MB", (int)((done + n) >> 20), (int)(bytes >> 20));
    }
    if (bytes > 4 * CHUNK) printf("\r\n");
    return bytes;
}

int main(void)
{
    const uint32_t rtc_freq = CHS_REGS->rtc_freq.f.ref_freq;
    const uint64_t core_freq = clint_get_core_freq(rtc_freq, 2500);
    const uint32_t read = CHS_REGS->scratch[0].w;
    void *priv = (void *)(uintptr_t)CHS_REGS->scratch[1].w;
    uint64_t t0, t1;
    const uint8_t *h = (const uint8_t *)DOOM_WAD_ADDR;
    rgp = (void *)(uintptr_t)CHS_REGS->scratch[3].w;
    uart_init(&__uart_base_addr__, core_freq, __BOOT_BAUDRATE);
    printf("\r\n[DOOM loader] boot mode %d, core %d Hz\r\n", (int)CHS_REGS->boot_mode.f.boot_mode, (int)core_freq);
    if (!(read & 1)) {
        printf("[DOOM loader] not started from a GPT disk (scratch[0] = 0x%x)\r\n", read);
        halt();
    }
    rread = (gpt_read_t)(void *)(uintptr_t)(read & ~1u);
    t0 = clint_get_mtime();
    load(priv, __BOOT_FW_TYPE_GUID, DOOM_PROG_ADDR, "program");
    load(priv, DOOM_WAD_TYPE_GUID, DOOM_WAD_ADDR, "DOOM WAD");
    t1 = clint_get_mtime();
    if (h[0] != 'G' || h[1] != '2' || h[2] != 'D' || h[3] != 'W') {
        printf("[DOOM loader] the WAD partition does not start with the G2DWAD header\r\n");
        halt();
    }
    printf("[DOOM loader] loaded in %d ms, starting DOOM\r\n", (int)((t1 - t0) * 1000 / rtc_freq));
    uart_write_flush(&__uart_base_addr__);
    __asm__ volatile("fence" ::: "memory");   // write the loaded images back from the data cache
    fencei();
    return ((entry_t)DOOM_PROG_ADDR)();
}

// A trap here (e.g. a bad SD read routine) is reported and stops.
void trap_vector(void)
{
    uint64_t mcause, mepc, mtval;
    __asm__ volatile("csrr %0, mcause; csrr %1, mepc; csrr %2, mtval" : "=r"(mcause), "=r"(mepc), "=r"(mtval));
    printf("\r\n[DOOM loader] trap mcause 0x%lx mepc 0x%lx mtval 0x%lx\r\n", mcause, mepc, mtval);
    halt();
}
