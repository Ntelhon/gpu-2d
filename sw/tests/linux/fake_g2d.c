// SPDX-License-Identifier: Apache-2.0
/* Fake /dev/g2d (v2.0 host test): what sw/linux/g2d_drv.c does, over the model HAL (built with its g2d_hal_open
 * renamed to g2d_model_hal_open): one open at a time (EBUSY), a DMA pool from the model's memory with the GPU address
 * window set to exactly the pool, refused writes to ADDR_WIN_LO / HI and MEM_BASE_HI (EPERM), pool allocations
 * (first fit, 64-byte granules, zeroed), WAIT = the level interrupt acknowledged and counted (the model executes a
 * kick at once, so a wait never sleeps), SYNC = nothing (the model is coherent). */
#include <errno.h>
#include <stdarg.h>
#include <stdint.h>
#include <string.h>
#include <sys/mman.h>

#include "fake_g2d.h"
#include "g2d_hal.h"
#include "generated/g2d_regs.h"
#include "uapi/g2d_ioctl.h"

const g2d_hal_t *g2d_model_hal_open(void);
void g2d_model_hal_close(const g2d_hal_t *hal);

enum { FD = 42, POOL = 16u << 20, GRAN = 64, NBLK = 4096 };

static const g2d_hal_t *m;
static int opened;
static uint8_t *pool_cpu;
static uint32_t pool_gpu;
static unsigned irqs;
static struct { uint64_t off, size; } blk[NBLK];   /* live allocations sorted by offset */
static int nblk;

unsigned fake_irq_count(void) { return irqs; }

int fake_open(const char *path, int flags, ...)
{
    void *cpu;
    (void)path;
    (void)flags;
    if (opened) {
        errno = EBUSY;
        return -1;
    }
    if (!m) {   /* "probe": model, pool, window (once, as the kernel module at load time) */
        m = g2d_model_hal_open();
        if (!m || m->mem_alloc(m->ctx, POOL, 4096, &cpu, &pool_gpu)) {
            errno = ENODEV;
            return -1;
        }
        pool_cpu = (uint8_t *)cpu;
        m->reg_write(m->ctx, G2D_CSR_MEM_BASE_HI, 0);
        m->reg_write(m->ctx, G2D_CSR_ADDR_WIN_LO, pool_gpu & ~0x3Fu);
        m->reg_write(m->ctx, G2D_CSR_ADDR_WIN_HI, (pool_gpu + POOL - 1u) & ~0x3Fu);
    }
    opened = 1;
    nblk = 0;                          /* allocations of the previous opener were freed at close */
    return FD;
}

int fake_close(int fd)
{
    if (fd != FD || !opened) {
        errno = EBADF;
        return -1;
    }
    opened = 0;
    nblk = 0;
    return 0;
}

void *fake_mmap(void *addr, size_t len, int prot, int flags, int fd, off_t off)
{
    (void)addr;
    (void)prot;
    (void)flags;
    if (fd != FD || off != 0 || len > POOL) {
        errno = EINVAL;
        return MAP_FAILED;
    }
    return pool_cpu;
}

int fake_munmap(void *addr, size_t len)
{
    (void)addr;
    (void)len;
    return 0;
}

static int alloc(struct g2d_alloc *a)
{
    uint64_t align = a->align < GRAN ? GRAN : a->align, size = (a->size + GRAN - 1) & ~(uint64_t)(GRAN - 1), pos = 0;
    int i, k;
    if (a->size == 0 || a->size > POOL || (align & (align - 1)) || nblk == NBLK)
        return -EINVAL;
    for (i = 0; i < nblk; i++) {
        uint64_t st = (pos + align - 1) & ~(align - 1);
        if (st + size <= blk[i].off) {
            pos = st;
            goto found;
        }
        pos = blk[i].off + blk[i].size;
    }
    pos = (pos + align - 1) & ~(align - 1);
    if (pos + size > POOL)
        return -ENOMEM;
found:
    for (k = nblk; k > i; k--)
        blk[k] = blk[k - 1];
    blk[i].off = pos;
    blk[i].size = size;
    nblk++;
    memset(pool_cpu + pos, 0, size);
    a->offset = pos;
    return 0;
}

static int release(uint64_t off)
{
    for (int i = 0; i < nblk; i++)
        if (blk[i].off == off) {
            for (; i + 1 < nblk; i++)
                blk[i] = blk[i + 1];
            nblk--;
            return 0;
        }
    return -EINVAL;
}

int fake_ioctl(int fd, unsigned long req, ...)
{
    va_list ap;
    void *arg;
    int rc = 0;
    va_start(ap, req);
    arg = va_arg(ap, void *);
    va_end(ap);
    if (fd != FD || !opened) {
        errno = EBADF;
        return -1;
    }
    switch (req) {
    case G2D_IOC_INFO: {
        struct g2d_info *i = (struct g2d_info *)arg;
        memset(i, 0, sizeof *i);
        i->ioctl_version = G2D_IOCTL_VERSION;
        i->id = m->reg_read(m->ctx, G2D_CSR_ID);
        i->version = m->reg_read(m->ctx, G2D_CSR_VERSION);
        i->hwcfg = m->reg_read(m->ctx, G2D_CSR_HWCFG);
        i->pool_size = POOL;
        i->pool_gpu = pool_gpu;
        i->mem_base_hi = 0;
        break;
    }
    case G2D_IOC_REG_READ:
    case G2D_IOC_REG_WRITE: {
        struct g2d_reg *r = (struct g2d_reg *)arg;
        if (r->off >= 4096 || (r->off & 3)) {
            rc = -EINVAL;
        } else if (req == G2D_IOC_REG_READ) {
            r->value = m->reg_read(m->ctx, r->off);
        } else if (r->off == G2D_CSR_ADDR_WIN_LO || r->off == G2D_CSR_ADDR_WIN_HI || r->off == G2D_CSR_MEM_BASE_HI) {
            rc = -EPERM;
        } else {
            m->reg_write(m->ctx, r->off, r->value);
        }
        break;
    }
    case G2D_IOC_ALLOC:
        rc = alloc((struct g2d_alloc *)arg);
        break;
    case G2D_IOC_FREE:
        rc = release(((struct g2d_alloc *)arg)->offset);
        break;
    case G2D_IOC_WAIT: {
        struct g2d_wait *w = (struct g2d_wait *)arg;
        uint32_t st = m->reg_read(m->ctx, G2D_CSR_IRQ_STATUS) & m->reg_read(m->ctx, G2D_CSR_IRQ_ENABLE);
        if (st) {                      /* the "interrupt handler" */
            m->reg_write(m->ctx, G2D_CSR_IRQ_STATUS, st);
            irqs++;
        }
        w->irq_count = irqs;
        break;
    }
    case G2D_IOC_SYNC:
        break;
    default:
        rc = -ENOTTY;
    }
    if (rc) {
        errno = -rc;
        return -1;
    }
    return 0;
}
