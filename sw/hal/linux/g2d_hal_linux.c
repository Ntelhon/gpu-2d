// SPDX-License-Identifier: Apache-2.0
/* G2D HAL, Linux user-space backend (v2.0, docs/PROGRAMMING.md §7.2) over the kernel driver /dev/g2d
 * (sw/linux/g2d_drv.c, ioctls in sw/linux/uapi/g2d_ioctl.h):
 *   CSRs           G2D_IOC_REG_READ / REG_WRITE (the kernel refuses writes to the address window registers)
 *   memory         the kernel's DMA pool, mapped once (mmap); buffers are pool ranges (G2D_IOC_ALLOC / FREE)
 *   cache          G2D_IOC_SYNC
 *   wait           G2D_IOC_WAIT (next G2D interrupt or the timeout; the last seen count avoids a lost wake-up)
 *   time_us        CLOCK_MONOTONIC;  mem_base_hi  from G2D_IOC_INFO
 *   lock           a pthread mutex (several threads of one process; only one process may open /dev/g2d)
 * Device node: $G2D_DEVICE or /dev/g2d. For the host test the system calls go to a fake device
 * (sw/tests/linux/fake_g2d.c) when built with G2D_LINUX_FAKE. */
#include <errno.h>
#include <fcntl.h>
#include <pthread.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <time.h>
#include <unistd.h>

#include "g2d_hal.h"
#include "uapi/g2d_ioctl.h"

#ifdef G2D_LINUX_FAKE
#include "fake_g2d.h"   /* fake_open / fake_ioctl / fake_mmap / fake_munmap / fake_close */
#define SYS_OPEN fake_open
#define SYS_IOCTL fake_ioctl
#define SYS_MMAP fake_mmap
#define SYS_MUNMAP fake_munmap
#define SYS_CLOSE fake_close
#else
#define SYS_OPEN open
#define SYS_IOCTL ioctl
#define SYS_MMAP mmap
#define SYS_MUNMAP munmap
#define SYS_CLOSE close
#endif

typedef struct {
    int fd;
    struct g2d_info info;
    uint8_t *pool;
    uint32_t irq_seen;
} lnx_t;

static lnx_t g_lnx = { -1, { 0, 0, 0, 0, 0, 0, 0 }, NULL, 0 };
static pthread_mutex_t g_mx = PTHREAD_MUTEX_INITIALIZER;

void g2d_hal_lock(void) { pthread_mutex_lock(&g_mx); }
void g2d_hal_unlock(void) { pthread_mutex_unlock(&g_mx); }

static uint32_t reg_read(void *ctx, uint32_t off)
{
    lnx_t *l = (lnx_t *)ctx;
    struct g2d_reg r = { off, 0 };
    if (SYS_IOCTL(l->fd, G2D_IOC_REG_READ, &r) != 0)
        return 0xFFFFFFFFu;
    return r.value;
}

static void reg_write(void *ctx, uint32_t off, uint32_t v)
{
    lnx_t *l = (lnx_t *)ctx;
    struct g2d_reg r = { off, v };
    (void)SYS_IOCTL(l->fd, G2D_IOC_REG_WRITE, &r);
}

static int mem_alloc(void *ctx, size_t size, size_t align, void **cpu, uint32_t *gpu)
{
    lnx_t *l = (lnx_t *)ctx;
    struct g2d_alloc a = { size, align < 64 ? 64 : align, 0 };
    if (SYS_IOCTL(l->fd, G2D_IOC_ALLOC, &a) != 0)
        return -1;
    *cpu = l->pool + a.offset;
    *gpu = l->info.pool_gpu + (uint32_t)a.offset;
    return 0;
}

static void mem_free(void *ctx, void *cpu, uint32_t gpu)
{
    lnx_t *l = (lnx_t *)ctx;
    struct g2d_alloc a = { 0, 0, (uint64_t)((uint8_t *)cpu - l->pool) };
    (void)gpu;
    (void)SYS_IOCTL(l->fd, G2D_IOC_FREE, &a);
}

static void *mem_cpu(void *ctx, uint32_t gpu, size_t size)
{
    lnx_t *l = (lnx_t *)ctx;
    uint64_t off = (uint64_t)(uint32_t)(gpu - l->info.pool_gpu);
    if (off >= l->info.pool_size || size > l->info.pool_size - off)
        return NULL;                                /* outside the pool: the GPU cannot reach it anyway */
    return l->pool + off;
}

static void sync_range(lnx_t *l, const void *cpu, size_t size, uint32_t to_device)
{
    struct g2d_sync s;
    memset(&s, 0, sizeof s);
    s.offset = (uint64_t)((const uint8_t *)cpu - l->pool);
    s.size = size;
    s.to_device = to_device;
    if (s.offset < l->info.pool_size && size <= l->info.pool_size - s.offset)
        (void)SYS_IOCTL(l->fd, G2D_IOC_SYNC, &s);
}

static void cache_clean(void *ctx, const void *cpu, size_t size) { sync_range((lnx_t *)ctx, cpu, size, 1); }
static void cache_invalidate(void *ctx, void *cpu, size_t size) { sync_range((lnx_t *)ctx, cpu, size, 0); }

static void hal_wait(void *ctx, uint32_t timeout_us)
{
    lnx_t *l = (lnx_t *)ctx;
    struct g2d_wait w = { timeout_us, l->irq_seen };
    if (SYS_IOCTL(l->fd, G2D_IOC_WAIT, &w) == 0)
        l->irq_seen = w.irq_count;
}

static uint64_t hal_time_us(void *ctx)
{
    struct timespec ts;
    (void)ctx;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000000u + (uint64_t)ts.tv_nsec / 1000u;
}

static uint32_t hal_mem_base_hi(void *ctx) { return ((lnx_t *)ctx)->info.mem_base_hi; }

static const g2d_hal_t g_hal = { &g_lnx, reg_read, reg_write, mem_alloc, mem_free, mem_cpu, cache_clean,
                                 cache_invalidate, hal_wait, hal_time_us, hal_mem_base_hi };

const g2d_hal_t *g2d_hal_open(void)
{
    const char *dev = getenv("G2D_DEVICE");
    lnx_t *l = &g_lnx;
    void *p;
    if (l->fd >= 0)
        return &g_hal;
    l->fd = SYS_OPEN(dev ? dev : "/dev/g2d", O_RDWR | O_CLOEXEC);
    if (l->fd < 0)
        return NULL;
    if (SYS_IOCTL(l->fd, G2D_IOC_INFO, &l->info) != 0 || l->info.ioctl_version != G2D_IOCTL_VERSION) {
        SYS_CLOSE(l->fd);
        l->fd = -1;
        return NULL;
    }
    p = SYS_MMAP(NULL, (size_t)l->info.pool_size, PROT_READ | PROT_WRITE, MAP_SHARED, l->fd, 0);
    if (p == MAP_FAILED) {
        SYS_CLOSE(l->fd);
        l->fd = -1;
        return NULL;
    }
    l->pool = (uint8_t *)p;
    l->irq_seen = 0;
    return &g_hal;
}

void g2d_hal_close(const g2d_hal_t *hal)
{
    lnx_t *l = &g_lnx;
    (void)hal;
    if (l->fd < 0)
        return;
    SYS_MUNMAP(l->pool, (size_t)l->info.pool_size);
    SYS_CLOSE(l->fd);
    l->fd = -1;
}
