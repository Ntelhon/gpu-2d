#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""SPDX licence identifiers of the repository (user decision 2026-11-12; LICENSE, LICENSES/):
  hardware descriptions (SystemVerilog, timing constraints)        Apache-2.0 WITH SHL-2.1
  software (Python, C / C++, scripts, build files)                  Apache-2.0
  third-party / differently licensed paths (THIRD_PARTY.md)         untouched (keep their own headers)

The Solderpad Hardware License v2.1 is an Apache-2.0 wraparound; the SPDX list carries it as the exception SHL-2.1,
so its expression is `Apache-2.0 WITH SHL-2.1` (LICENSES/SHL-2.1.txt). A bare `SHL-2.1` is not a licence identifier.

  spdx.py --apply    add the identifier line to every tracked source file that has none
  spdx.py --check    exit 1 if a tracked source file has no identifier, or a hardware file a different one than
                     its default (the dual-licensed paths of LICENSE keep their own software expression)
The generators call add() so that generated files carry the line too.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TAG = "SPDX-License-Identifier:"

HW_LICENCE = "Apache-2.0 WITH SHL-2.1"
HW = {".sv", ".svh", ".sdc", ".xdc"}
SW_SLASH = {".c", ".h", ".cpp", ".hpp", ".cc"}
SW_BLOCK = {".ld", ".S"}
SW_HASH = {".py", ".sh", ".tcl", ".cmake"}
SW_NAMES = {"Makefile", "CMakeLists.txt"}

# third-party or differently licensed material (THIRD_PARTY.md): never tagged here
EXCLUDE = ("fpga/cheshire/axi_vga_g2d/", "sw/apps/doom/", "sw/include/vg_lite.h", "LICENSES/")


def licence_and_style(rel: str):
    p = Path(rel)
    if any(rel.startswith(e) or rel == e for e in EXCLUDE):
        return None
    if p.suffix in HW:
        return HW_LICENCE, "//" if p.suffix in (".sv", ".svh") else "#"
    if p.suffix in SW_SLASH:
        return "Apache-2.0", "//"
    if p.suffix in SW_BLOCK:
        return "Apache-2.0", "/*"
    if p.suffix in SW_HASH or p.name in SW_NAMES:
        return "Apache-2.0", "#"
    return None


def line(lic: str, style: str) -> str:
    return f"/* {TAG} {lic} */" if style == "/*" else f"{style} {TAG} {lic}"


def add(rel: str, text: str) -> str:
    """text with the identifier line for rel (after a shebang line), unchanged if it has one or rel is excluded."""
    ls = licence_and_style(rel)
    if ls is None or TAG in "\n".join(text.splitlines()[:3]):
        return text
    ln = line(*ls)
    if text.startswith("#!"):
        first, _, rest = text.partition("\n")
        return f"{first}\n{ln}\n{rest}"
    return f"{ln}\n{text}"


def tracked():
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return [f for f in out.splitlines() if (ROOT / f).is_file()]


def main(argv) -> int:
    mode = argv[1] if len(argv) > 1 else "--check"
    missing, wrong = [], []
    for rel in tracked():
        if licence_and_style(rel) is None:
            continue
        p = ROOT / rel
        text = p.read_text(errors="surrogateescape")
        head = "\n".join(text.splitlines()[:3])
        if TAG in head:
            lic = head.split(TAG, 1)[1].splitlines()[0].strip().rstrip("*/").strip()
            if Path(rel).suffix in HW and lic != HW_LICENCE:
                wrong.append(f"{rel} ({lic})")
            continue
        if mode == "--apply":
            p.write_text(add(rel, text), errors="surrogateescape")
        else:
            missing.append(rel)
    for m in missing:
        print(f"spdx: no {TAG} line: {m}")
    for w in wrong:
        print(f"spdx: hardware file not under {HW_LICENCE}: {w}")
    return 1 if missing or wrong else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
