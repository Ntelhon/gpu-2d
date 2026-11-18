# SPDX-License-Identifier: Apache-2.0
"""Bare-metal HAL (sw/hal/baremetal) compile check: driver + geometry + HAL for RV64GC with the SiFive GCC
(`make baremetal-check`, -Werror -pedantic). Skipped when the RISC-V toolchain is not on PATH."""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.skipif(shutil.which("riscv64-unknown-elf-gcc") is None, reason="riscv64-unknown-elf-gcc not on PATH")
def test_baremetal_compiles():
    p = subprocess.run(["make", "-C", str(ROOT), "baremetal-check"], capture_output=True, text=True)
    assert p.returncode == 0, p.stdout[-3000:] + p.stderr[-3000:]
    assert (ROOT / "build" / "baremetal" / "libg2d_vglite.a").exists()
