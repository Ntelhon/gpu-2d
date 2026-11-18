#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Build and check the G2D Linux kernel module (v2.0 step B, docs/PROGRAMMING.md §7.2).

For each configured kernel tree given with --kdir (prepared with `make modules_prepare`):
  1. stage sw/linux (driver, Kbuild), sw/linux/uapi and sw/include/generated/g2d_regs.h into
     build/linux/<kernel version>/ (no build output under sw/);
  2. `make M=... modules W=1` with ARCH=riscv and the given CROSS_COMPILE; KBUILD_MODPOST_WARN=1 because a
     modules_prepare tree has no Module.symvers (no full kernel build: the vDSO needs a Linux-target toolchain);
  3. export check: every undefined symbol of g2d.ko must be exported by that kernel (EXPORT_SYMBOL* in its
     sources, or one of the few symbols exported through a macro, listed in MACRO_EXPORTS);
  4. compiler warnings fail the build.

  build_kmod.py --kdir ~/tools/src/linux-v5.10.7 [--kdir ...] [--cross riscv64-unknown-elf-]
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Exported by the kernel through a wrapper macro, so a plain EXPORT_SYMBOL grep misses them (checked by hand in
# 5.10.7 and 6.12.117: arch/riscv/kernel/... DEFINE_PER_CPU + EXPORT_PER_CPU_SYMBOL, drivers/base/core.c
# define_dev_printk_level, kernel/params.c EXPORT_SYMBOL(param_ops_##name) via STANDARD_PARAM_DEF).
MACRO_EXPORTS = {"__mmiowb_state", "_dev_err", "_dev_info", "_dev_warn", "_dev_notice", "param_ops_uint",
                 "param_ops_int", "param_ops_bool"}
# Provided by the module linker / the module itself, not imported from the kernel.
LOCAL = {"__this_module"}

EXPORT_RE = re.compile(rb"EXPORT_(?:PER_CPU_)?SYMBOL(?:_GPL)?(?:_NS(?:_GPL)?)?\(\s*(\w+)")


def run(cmd, **kw):
    return subprocess.run(cmd, check=True, text=True, capture_output=True, **kw)


def kernel_version(kdir: Path) -> str:
    return run(["make", "-s", "-C", str(kdir), "kernelversion"]).stdout.strip()


def exported(kdir: Path) -> set[str]:
    out = set()
    for f in kdir.rglob("*.[chS]"):
        if f.is_symlink():
            continue
        try:
            b = f.read_bytes()
        except OSError:
            continue
        if b"EXPORT_" in b:
            out.update(m.decode() for m in EXPORT_RE.findall(b))
    return out


def stage(dst: Path) -> None:
    if dst.exists():
        shutil.rmtree(dst)
    (dst / "generated").mkdir(parents=True)
    (dst / "uapi").mkdir()
    shutil.copy(ROOT / "sw/linux/g2d_drv.c", dst)
    shutil.copy(ROOT / "sw/linux/uapi/g2d_ioctl.h", dst / "uapi")
    shutil.copy(ROOT / "sw/include/generated/g2d_regs.h", dst / "generated")
    kb = (ROOT / "sw/linux/Kbuild").read_text().replace("ccflags-y := -I$(src) -I$(src)/../include",
                                                       "ccflags-y := -I$(src)")
    (dst / "Kbuild").write_text(kb)


def build(kdir: Path, cross: str, nm: str) -> int:
    ver = kernel_version(kdir)
    dst = ROOT / "build/linux" / f"v{ver}"
    stage(dst)
    cmd = ["make", "-C", str(kdir), f"M={dst}", "ARCH=riscv", f"CROSS_COMPILE={cross}", "KBUILD_MODPOST_WARN=1",
           "W=1", "modules"]
    p = subprocess.run(cmd, text=True, capture_output=True)
    log = p.stdout + p.stderr
    (dst / "build.log").write_text(log)
    if p.returncode != 0 or not (dst / "g2d.ko").exists():
        print(log[-3000:])
        print(f"FAIL {ver}: build (log {dst / 'build.log'})")
        return 1
    warns = [l for l in log.splitlines() if "warning:" in l and "modpost" not in l]
    undef = {l.split()[-1] for l in run([nm, "-u", str(dst / "g2d.ko")]).stdout.splitlines() if l.strip()} - LOCAL
    missing = sorted(undef - exported(kdir) - MACRO_EXPORTS)
    bad = 0
    for w in warns:
        print(f"  {w}")
        bad = 1
    if missing:
        print(f"  not exported by {ver}: {', '.join(missing)}")
        bad = 1
    print(f"{'FAIL' if bad else 'PASS'} {ver}: g2d.ko, {len(undef)} imported symbols, {len(warns)} warnings "
          f"({dst / 'g2d.ko'})")
    return bad


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--kdir", action="append", required=True, type=Path)
    ap.add_argument("--cross", default="riscv64-unknown-elf-")
    a = ap.parse_args(argv)
    nm = a.cross + "nm"
    if not shutil.which(nm):
        print(f"error: {nm} not in PATH")
        return 2
    return max(build(k.expanduser().resolve(), a.cross, nm) for k in a.kdir)


if __name__ == "__main__":
    sys.exit(main())
