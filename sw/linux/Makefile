# SPDX-License-Identifier: GPL-2.0-only OR MIT
# Out-of-tree build of the G2D kernel module (v2.0):
#   make -C sw/linux KDIR=<configured kernel tree> ARCH=riscv CROSS_COMPILE=riscv64-unknown-elf-
KDIR ?= /lib/modules/$(shell uname -r)/build
all:
	$(MAKE) -C $(KDIR) M=$(CURDIR) modules
clean:
	$(MAKE) -C $(KDIR) M=$(CURDIR) clean
