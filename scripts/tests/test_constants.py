# SPDX-License-Identifier: Apache-2.0
"""The command constants of spec/regs.yaml ("constants") against the copies that are not generated from it:
the RTL package (opcodes, the set of valid format codes). The C header is generated from the YAML,
dv/common/g2d_cmd.py reads it, and the golden model takes its opcodes from the C header."""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import gen_regs  # noqa: E402

C = {g: vals for g, (_, _, vals) in gen_regs.load(ROOT / "spec" / "regs.yaml").constants.items()}


def test_rtl_package_opcodes_and_formats():
    pkg = (ROOT / "rtl" / "pkg" / "g2d_pkg.sv").read_text()
    ops = {m.group(1): int(m.group(2), 16) for m in re.finditer(r"localparam logic \[7:0\] OP_(\w+)\s*=\s*8'h([0-9A-Fa-f]+);", pkg)}
    assert ops == C["opcode"]
    body = re.search(r"function automatic logic fmt_valid.*?endfunction", pkg, re.S).group(0)
    codes = {int(h, 16) for h in re.findall(r"8'h([0-9A-Fa-f]{2})", body)}
    assert codes == set(C["format"].values())


def test_model_opcodes_from_header():
    src = (ROOT / "model" / "src" / "gpu.cpp").read_text()
    enum = re.search(r"enum Opcode : uint32_t \{(.*?)\};", src, re.S).group(1)
    assert set(re.findall(r"G2D_OP_(\w+)", enum)) == set(C["opcode"])
    assert not re.search(r"0x[0-9A-Fa-f]+", enum)


def test_c_header_has_every_constant():
    h = (ROOT / "sw" / "include" / "generated" / "g2d_regs.h").read_text()
    pre = {g: p for g, (p, _, _) in gen_regs.load(ROOT / "spec" / "regs.yaml").constants.items()}
    for g, vals in C.items():
        for k, v in vals.items():
            assert f"#define G2D_{pre[g]}_{k} 0x{v:02X}u" in h, (g, k)
