// SPDX-License-Identifier: Apache-2.0
/* Fake /dev/g2d for the host test of the Linux HAL (v2.0): the kernel driver's semantics (sw/linux/g2d_drv.c) over
 * the golden model. */
#ifndef FAKE_G2D_H
#define FAKE_G2D_H
#include <stddef.h>
#include <sys/types.h>

int fake_open(const char *path, int flags, ...);
int fake_ioctl(int fd, unsigned long req, ...);
void *fake_mmap(void *addr, size_t len, int prot, int flags, int fd, off_t off);
int fake_munmap(void *addr, size_t len);
int fake_close(int fd);
unsigned fake_irq_count(void);

#endif
