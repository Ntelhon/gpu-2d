/* SPDX-License-Identifier: GPL-2.0-only OR MIT */
/* G2D Linux driver interface (v2.0, docs/PROGRAMMING.md §7.2): ioctls of /dev/g2d (sw/linux/g2d_drv.c), used by the
 * Linux HAL of the vg_lite driver (sw/hal/linux). Shared by the kernel module and user space.
 *
 * Memory model: at probe the module allocates one DMA pool (module parameter pool_mb, default 32 MB) and programs
 * the GPU address window (ADDR_WIN_LO / HI, MEM_BASE_HI) to exactly that pool, so the GPU cannot reach any other
 * memory whatever user space submits. Buffers are offsets in the pool; mmap(fd, offset 0, pool size) maps the pool.
 * GPU address of a buffer = pool_gpu + offset (low 32 bits; the window's upper bits are in MEM_BASE_HI). */
#ifndef G2D_IOCTL_H
#define G2D_IOCTL_H

#ifdef __KERNEL__
#include <linux/ioctl.h>
#include <linux/types.h>
#else
#include <stdint.h>
#include <sys/ioctl.h>
typedef uint32_t __u32;
typedef uint64_t __u64;
#endif

#define G2D_IOCTL_VERSION 1u

struct g2d_info {             /* G2D_IOC_INFO (out) */
    __u32 ioctl_version;      /* G2D_IOCTL_VERSION */
    __u32 id, version, hwcfg; /* the ID / VERSION / HWCFG registers */
    __u64 pool_size;          /* bytes of the DMA pool (mmap length) */
    __u32 pool_gpu;           /* GPU address (low 32 bits) of pool offset 0 */
    __u32 mem_base_hi;        /* upper 32 bits of all GPU addresses */
};

struct g2d_reg {              /* G2D_IOC_REG_READ (in: off; out: value), G2D_IOC_REG_WRITE (in: off, value) */
    __u32 off;                /* byte offset in the 4 KB CSR window, multiple of 4 */
    __u32 value;
};

struct g2d_alloc {            /* G2D_IOC_ALLOC (in: size, align; out: offset), G2D_IOC_FREE (in: offset) */
    __u64 size;
    __u64 align;              /* power of two; at least 64 */
    __u64 offset;             /* in the pool */
};

struct g2d_wait {             /* G2D_IOC_WAIT (in: timeout_us; out: irq_count) */
    __u32 timeout_us;         /* returns after the next G2D interrupt or the timeout (0: return at once) */
    __u32 irq_count;          /* interrupts taken since probe */
};

struct g2d_sync {             /* G2D_IOC_SYNC (in): make CPU writes visible to the GPU / GPU writes to the CPU */
    __u64 offset, size;       /* range in the pool */
    __u32 to_device;          /* 1: clean (before the GPU reads), 0: invalidate (before the CPU reads) */
    __u32 pad;
};

#define G2D_IOC_MAGIC 'G'
#define G2D_IOC_INFO _IOR(G2D_IOC_MAGIC, 0, struct g2d_info)
#define G2D_IOC_REG_READ _IOWR(G2D_IOC_MAGIC, 1, struct g2d_reg)
#define G2D_IOC_REG_WRITE _IOW(G2D_IOC_MAGIC, 2, struct g2d_reg)
#define G2D_IOC_ALLOC _IOWR(G2D_IOC_MAGIC, 3, struct g2d_alloc)
#define G2D_IOC_FREE _IOW(G2D_IOC_MAGIC, 4, struct g2d_alloc)
#define G2D_IOC_WAIT _IOWR(G2D_IOC_MAGIC, 5, struct g2d_wait)
#define G2D_IOC_SYNC _IOW(G2D_IOC_MAGIC, 6, struct g2d_sync)

#endif /* G2D_IOCTL_H */
