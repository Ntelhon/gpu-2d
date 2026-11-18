// SPDX-License-Identifier: GPL-2.0-only OR MIT
/* G2D Linux kernel driver (v2.0, docs/PROGRAMMING.md §7.2): /dev/g2d for the vg_lite driver's Linux HAL
 * (sw/hal/linux). Dual licence GPL-2.0-only OR MIT: the kernel requires a GPL-compatible module licence for the
 * symbols it uses; MIT keeps it permissive (docs/OPEN_QUESTIONS.md Q-LNX-2). uapi/g2d_ioctl.h has the same licence.
 *
 * - Platform device from the device tree (compatible "g2d,g2d", binding sw/linux/bindings/gpu/g2d.yaml): one MMIO
 *   region (the 4 KB CSR window) and one interrupt.
 * - DMA pool: one coherent allocation at probe (module parameter pool_mb, default 32, halved down to 4 MB if the
 *   allocation fails); ADDR_WIN_LO / HI and MEM_BASE_HI are set to exactly the pool, so the GPU cannot access any
 *   other memory (arch.md §4.2, rendering.md §13.2), whatever command buffers user space builds. Those three
 *   registers cannot be written from user space.
 * - ioctls (uapi/g2d_ioctl.h): INFO, REG_READ / REG_WRITE (CSR access), ALLOC / FREE (pool ranges, freed when the
 *   file is closed), WAIT (next interrupt or timeout), SYNC (cache maintenance); mmap maps the pool.
 * - One open file at a time (-EBUSY otherwise): the vg_lite driver assumes it owns the GPU (fence sequence, soft-reset
 *   recovery); threads of that process share it through the driver lock (§6.1). Several processes would need a
 *   command scheduler in the kernel (not in v2.0).
 * - Interrupt: the level interrupt is acknowledged in the handler (IRQ_STATUS written back), the interrupt count is
 *   incremented and the waiters woken; the vg_lite driver tracks progress through FENCE_SEQ / STATUS.
 * - Cache maintenance (SYNC): a full fence. On Cheshire's CVA6 (DcacheFlushOnFence) a fence writes back and
 *   invalidates the D-cache; elsewhere the coherent pool needs nothing more (OPEN_QUESTIONS Q-LNX-1).
 * Access: the device node should be limited to trusted users (as for UIO); the window confines the GPU, not the
 * register interface's other effects (e.g. a GPU soft reset). */
#include <linux/atomic.h>
#include <linux/dma-mapping.h>
#include <linux/fs.h>
#include <linux/genalloc.h>
#include <linux/interrupt.h>
#include <linux/io.h>
#include <linux/miscdevice.h>
#include <linux/mm.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/of.h>
#include <linux/platform_device.h>
#include <linux/slab.h>
#include <linux/uaccess.h>
#include <linux/version.h>
#include <linux/wait.h>

#include "generated/g2d_regs.h"
#include "uapi/g2d_ioctl.h"

#define G2D_ID_VALUE 0x47324420u

static unsigned int pool_mb = 32;
module_param(pool_mb, uint, 0444);
MODULE_PARM_DESC(pool_mb, "GPU memory pool in MB (the GPU can access only this memory)");

struct g2d_dev {
    struct device *dev;
    void __iomem *regs;
    int irq;
    struct miscdevice misc;
    void *pool_cpu;
    dma_addr_t pool_dma;
    size_t pool_size;
    struct gen_pool *pool;
    struct mutex lock;           /* pool allocations */
    atomic_t irqs;
    wait_queue_head_t wq;
    atomic_t opened;             /* one user at a time: the vg_lite driver owns the GPU (fences, soft reset) */
};

struct g2d_file {
    struct g2d_dev *g;
    struct list_head allocs;
};

struct g2d_alloc_rec {
    struct list_head node;
    unsigned long addr;          /* gen_pool address (= offset + 1, gen_pool does not hand out 0) */
    size_t size;
};

static u32 g2d_rd(struct g2d_dev *g, u32 off) { return readl(g->regs + off); }
static void g2d_wr(struct g2d_dev *g, u32 off, u32 v) { writel(v, g->regs + off); }

static irqreturn_t g2d_isr(int irq, void *arg)
{
    struct g2d_dev *g = arg;
    u32 st = g2d_rd(g, G2D_CSR_IRQ_STATUS) & g2d_rd(g, G2D_CSR_IRQ_ENABLE);
    if (!st)
        return IRQ_NONE;
    g2d_wr(g, G2D_CSR_IRQ_STATUS, st);     /* W1C: the level falls */
    atomic_inc(&g->irqs);
    wake_up_interruptible(&g->wq);
    return IRQ_HANDLED;
}

static int g2d_open(struct inode *inode, struct file *f)
{
    struct g2d_dev *g = container_of(f->private_data, struct g2d_dev, misc);
    struct g2d_file *gf;
    if (atomic_cmpxchg(&g->opened, 0, 1) != 0)
        return -EBUSY;
    gf = kzalloc(sizeof(*gf), GFP_KERNEL);
    if (!gf) {
        atomic_set(&g->opened, 0);
        return -ENOMEM;
    }
    gf->g = g;
    INIT_LIST_HEAD(&gf->allocs);
    f->private_data = gf;
    return 0;
}

static int g2d_release(struct inode *inode, struct file *f)
{
    struct g2d_file *gf = f->private_data;
    struct g2d_alloc_rec *a, *t;
    mutex_lock(&gf->g->lock);
    list_for_each_entry_safe(a, t, &gf->allocs, node) {
        gen_pool_free(gf->g->pool, a->addr, a->size);
        list_del(&a->node);
        kfree(a);
    }
    mutex_unlock(&gf->g->lock);
    atomic_set(&gf->g->opened, 0);
    kfree(gf);
    return 0;
}

/* Registers user space may not write: the address window confining the GPU to the pool. */
static bool g2d_reg_protected(u32 off)
{
    return off == G2D_CSR_ADDR_WIN_LO || off == G2D_CSR_ADDR_WIN_HI || off == G2D_CSR_MEM_BASE_HI;
}

static long g2d_ioctl(struct file *f, unsigned int cmd, unsigned long arg)
{
    struct g2d_file *gf = f->private_data;
    struct g2d_dev *g = gf->g;
    void __user *up = (void __user *)arg;

    switch (cmd) {
    case G2D_IOC_INFO: {
        struct g2d_info i = {0};
        i.ioctl_version = G2D_IOCTL_VERSION;
        i.id = g2d_rd(g, G2D_CSR_ID);
        i.version = g2d_rd(g, G2D_CSR_VERSION);
        i.hwcfg = g2d_rd(g, G2D_CSR_HWCFG);
        i.pool_size = g->pool_size;
        i.pool_gpu = lower_32_bits(g->pool_dma);
        i.mem_base_hi = upper_32_bits(g->pool_dma);
        return copy_to_user(up, &i, sizeof(i)) ? -EFAULT : 0;
    }
    case G2D_IOC_REG_READ:
    case G2D_IOC_REG_WRITE: {
        struct g2d_reg r;
        if (copy_from_user(&r, up, sizeof(r)))
            return -EFAULT;
        if (r.off >= 4096 || (r.off & 3))
            return -EINVAL;
        if (cmd == G2D_IOC_REG_READ) {
            r.value = g2d_rd(g, r.off);
            return copy_to_user(up, &r, sizeof(r)) ? -EFAULT : 0;
        }
        if (g2d_reg_protected(r.off))
            return -EPERM;
        g2d_wr(g, r.off, r.value);
        return 0;
    }
    case G2D_IOC_ALLOC: {
        struct g2d_alloc a;
        struct g2d_alloc_rec *rec;
        struct genpool_data_align al;
        if (copy_from_user(&a, up, sizeof(a)))
            return -EFAULT;
        if (a.size == 0 || a.size > g->pool_size || a.align > PAGE_SIZE || (a.align & (a.align - 1)))
            return -EINVAL;
        rec = kzalloc(sizeof(*rec), GFP_KERNEL);
        if (!rec)
            return -ENOMEM;
        al.align = a.align < 64 ? 64 : (int)a.align;
        rec->size = ALIGN(a.size, 64);
        mutex_lock(&g->lock);
        rec->addr = gen_pool_alloc_algo(g->pool, rec->size, gen_pool_first_fit_align, &al);
        if (rec->addr)
            list_add(&rec->node, &gf->allocs);
        mutex_unlock(&g->lock);
        if (!rec->addr) {
            kfree(rec);
            return -ENOMEM;
        }
        a.offset = rec->addr - PAGE_SIZE;            /* the pool is added at address PAGE_SIZE (see probe) */
        memset((u8 *)g->pool_cpu + a.offset, 0, rec->size);
        return copy_to_user(up, &a, sizeof(a)) ? -EFAULT : 0;
    }
    case G2D_IOC_FREE: {
        struct g2d_alloc a;
        struct g2d_alloc_rec *rec, *t;
        int found = 0;
        if (copy_from_user(&a, up, sizeof(a)))
            return -EFAULT;
        mutex_lock(&g->lock);
        list_for_each_entry_safe(rec, t, &gf->allocs, node) {
            if (rec->addr == a.offset + PAGE_SIZE) {
                gen_pool_free(g->pool, rec->addr, rec->size);
                list_del(&rec->node);
                kfree(rec);
                found = 1;
                break;
            }
        }
        mutex_unlock(&g->lock);
        return found ? 0 : -EINVAL;
    }
    case G2D_IOC_WAIT: {
        struct g2d_wait w;
        long left;
        if (copy_from_user(&w, up, sizeof(w)))
            return -EFAULT;
        /* wait until the count differs from the caller's last seen value (no lost wake-up) or the timeout */
        left = wait_event_interruptible_timeout(g->wq, (u32)atomic_read(&g->irqs) != w.irq_count,
                                                usecs_to_jiffies(w.timeout_us));
        if (left < 0)
            return left;                             /* signal */
        w.irq_count = (u32)atomic_read(&g->irqs);
        return copy_to_user(up, &w, sizeof(w)) ? -EFAULT : 0;
    }
    case G2D_IOC_SYNC: {
        struct g2d_sync s;
        if (copy_from_user(&s, up, sizeof(s)))
            return -EFAULT;
        if (s.offset > g->pool_size || s.size > g->pool_size - s.offset)
            return -EINVAL;
        mb();                                        /* CVA6 (DcacheFlushOnFence): write back + invalidate */
        return 0;
    }
    default:
        return -ENOTTY;
    }
}

static int g2d_mmap(struct file *f, struct vm_area_struct *vma)
{
    struct g2d_file *gf = f->private_data;
    struct g2d_dev *g = gf->g;
    if (vma->vm_pgoff != 0 || vma->vm_end - vma->vm_start > g->pool_size)
        return -EINVAL;
    return dma_mmap_coherent(g->dev, vma, g->pool_cpu, g->pool_dma, vma->vm_end - vma->vm_start);
}

static const struct file_operations g2d_fops = {
    .owner = THIS_MODULE,
    .open = g2d_open,
    .release = g2d_release,
    .unlocked_ioctl = g2d_ioctl,
    .compat_ioctl = g2d_ioctl,
    .mmap = g2d_mmap,
};

static int g2d_probe(struct platform_device *pdev)
{
    struct device *dev = &pdev->dev;
    struct g2d_dev *g;
    size_t size;
    u32 id;
    int ret;

    g = devm_kzalloc(dev, sizeof(*g), GFP_KERNEL);
    if (!g)
        return -ENOMEM;
    g->dev = dev;
    mutex_init(&g->lock);
    init_waitqueue_head(&g->wq);
    atomic_set(&g->irqs, 0);

    g->regs = devm_platform_ioremap_resource(pdev, 0);
    if (IS_ERR(g->regs))
        return PTR_ERR(g->regs);
    id = g2d_rd(g, G2D_CSR_ID);
    if (id != G2D_ID_VALUE) {
        dev_err(dev, "no G2D at this address (ID 0x%08x)\n", id);
        return -ENODEV;
    }
    g->irq = platform_get_irq(pdev, 0);
    if (g->irq < 0)
        return g->irq;

    ret = dma_set_mask_and_coherent(dev, DMA_BIT_MASK(64));
    if (ret)
        return ret;
    for (size = (size_t)pool_mb << 20; size >= (4u << 20); size >>= 1) {
        g->pool_cpu = dmam_alloc_coherent(dev, size, &g->pool_dma, GFP_KERNEL);
        if (g->pool_cpu)
            break;
    }
    if (!g->pool_cpu)
        return -ENOMEM;
    if (upper_32_bits(g->pool_dma) != upper_32_bits(g->pool_dma + size - 1)) {
        dev_err(dev, "the DMA pool crosses a 4 GB boundary\n");
        return -ENOMEM;
    }
    g->pool_size = size;
    g->pool = devm_gen_pool_create(dev, 6, -1, "g2d");          /* 64-byte granules */
    if (!g->pool)
        return -ENOMEM;
    ret = gen_pool_add(g->pool, PAGE_SIZE, size, -1);           /* addresses PAGE_SIZE..: 0 means "no memory" */
    if (ret)
        return ret;

    /* confine the GPU to the pool (CSR configuration survives the GPU's soft reset, arch.md §2.2) */
    g2d_wr(g, G2D_CSR_MEM_BASE_HI, upper_32_bits(g->pool_dma));
    g2d_wr(g, G2D_CSR_ADDR_WIN_LO, lower_32_bits(g->pool_dma) & ~0x3Fu);
    g2d_wr(g, G2D_CSR_ADDR_WIN_HI, (lower_32_bits(g->pool_dma) + (u32)size - 1u) & ~0x3Fu);
    g2d_wr(g, G2D_CSR_IRQ_STATUS, 0xFFFFFFFFu);

    ret = devm_request_irq(dev, g->irq, g2d_isr, IRQF_SHARED, "g2d", g);
    if (ret)
        return ret;

    g->misc.minor = MISC_DYNAMIC_MINOR;
    g->misc.name = "g2d";
    g->misc.fops = &g2d_fops;
    g->misc.parent = dev;
    g->misc.mode = 0660;
    ret = misc_register(&g->misc);
    if (ret)
        return ret;
    platform_set_drvdata(pdev, g);
    dev_info(dev, "G2D version 0x%06x, %zu MB pool at 0x%llx, irq %d\n", g2d_rd(g, G2D_CSR_VERSION), size >> 20,
             (unsigned long long)g->pool_dma, g->irq);
    return 0;
}

#if LINUX_VERSION_CODE >= KERNEL_VERSION(6, 11, 0)
static void g2d_remove(struct platform_device *pdev)
{
    struct g2d_dev *g = platform_get_drvdata(pdev);
    misc_deregister(&g->misc);
}
#else
static int g2d_remove(struct platform_device *pdev)
{
    struct g2d_dev *g = platform_get_drvdata(pdev);
    misc_deregister(&g->misc);
    return 0;
}
#endif

static const struct of_device_id g2d_of_match[] = {
    { .compatible = "g2d,g2d" },
    { }
};
MODULE_DEVICE_TABLE(of, g2d_of_match);

static struct platform_driver g2d_driver = {
    .probe = g2d_probe,
    .remove = g2d_remove,
    .driver = {
        .name = "g2d",
        .of_match_table = g2d_of_match,
    },
};
module_platform_driver(g2d_driver);

MODULE_DESCRIPTION("G2D 2D / 2.5D GPU");
MODULE_LICENSE("Dual MIT/GPL");
