# SPDX-License-Identifier: Apache-2.0
"""Unit tests for scripts/gen_regs.py (validation, outputs, generated C header)."""

import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import gen_regs  # noqa: E402

YAML = ROOT / "spec" / "regs.yaml"

MINIMAL = """
name: t
version: {major: 1, minor: 2, patch: 3}
blocks:
  csr:
    addr_bits: 12
    registers:
%s
  state:
    size: 4
    registers:
      - {name: S0, index: 0, fields: [{name: V, bits: "31:0", reset: 0}]}
"""


def _load_text(tmp_path, regs_yaml: str):
    p = tmp_path / "r.yaml"
    p.write_text(MINIMAL % textwrap.indent(textwrap.dedent(regs_yaml), "      "))
    return gen_regs.load(p)


def test_project_yaml_loads_and_is_consistent():
    m = gen_regs.load(YAML)
    assert m.csr[0].name == "ID" and m.csr[0].reset == 0x47324420
    ver = next(r for r in m.csr if r.name == "VERSION")
    assert ver.reset == (m.version["major"] << 16) | (m.version["minor"] << 8) | m.version["patch"]
    # every state index referenced by commands.md examples exists and is contiguous where relied upon
    idx = {r.name: r.addr for r in m.state}
    assert [idx[n] for n in ("TGT_ADDR", "TGT_STRIDE", "TGT_SIZE", "TGT_FMT")] == [0, 1, 2, 3]
    assert [idx[n] for n in ("BLEND", "ALPHA_CTRL", "PAINT_CTRL", "PAINT_COLOR")] == [8, 9, 10, 11]
    assert [idx[f"XF_{c}"] for c in "ABCDEFGHI"] == list(range(0x28, 0x31))


def test_generated_files_are_up_to_date():
    assert gen_regs.main(["--check"]) == 0


@pytest.mark.parametrize(
    "regs,msg",
    [
        ('- {name: A, offset: 0, fields: [{name: X, bits: "3:0", access: rw}, {name: Y, bits: "2", access: rw}]}', "overlaps"),
        ('- {name: A, offset: 0, fields: [{name: X, bits: "0", access: rw}]}\n'
         '- {name: B, offset: 0, fields: [{name: X, bits: "0", access: rw}]}', "duplicate offset"),
        ('- {name: A, offset: 2, fields: [{name: X, bits: "0", access: rw}]}', "not word aligned"),
        ('- {name: A, offset: 0, fields: [{name: X, bits: "32:0", access: rw}]}', "out of range"),
        ('- {name: A, offset: 0, fields: [{name: X, bits: "1:0", access: rw, reset: 4}]}', "does not fit"),
        ('- {name: A, offset: 0, fields: [{name: X, bits: "0", access: bogus}]}', "access must be"),
        ('- {name: A, offset: 0, fields: [{name: X, bits: "0", access: ro, reset: 0}]}', "no reset"),
        ('- {name: a, offset: 0, fields: [{name: X, bits: "0", access: rw}]}', "must match"),
        ('- {name: A, offset: 0x1000, fields: [{name: X, bits: "0", access: rw}]}', "outside 12-bit"),
    ],
)
def test_validation_errors(tmp_path, regs, msg):
    with pytest.raises(gen_regs.RegsError, match=msg):
        _load_text(tmp_path, regs)


def test_struct_layout_is_dense_and_disjoint():
    m = gen_regs.load(YAML)
    for which in ("reg2hw", "hw2reg"):
        lay = gen_regs.struct_layout(m, which)
        bits = set()
        for lsb, w in lay.values():
            span = set(range(lsb, lsb + w))
            assert not bits & span
            bits |= span
        assert bits == set(range(len(bits)))


@pytest.mark.skipif(shutil.which("gcc") is None, reason="gcc not found")
def test_c_header_compiles_and_matches_yaml(tmp_path):
    out = tmp_path / "out"
    assert gen_regs.main(["--root", str(out)]) == 0
    m = gen_regs.load(YAML)
    checks = []
    for r in m.csr:
        checks.append(f'CHECK(G2D_CSR_{r.name} == {r.addr}u && G2D_CSR_{r.name}_RESET == {r.reset}u);')
        for f in r.fields:
            checks.append(f"CHECK(G2D_CSR_{r.name}_{f.name}_MASK == {f.mask}u);")
    for r in m.state:
        checks.append(f"CHECK(G2D_ST_{r.name} == {r.addr}u && G2D_ST_{r.name}_RESET == {r.reset}u);")
    src = tmp_path / "t.c"
    src.write_text(
        '#include <stdio.h>\n#include "g2d_regs.h"\n'
        "#define CHECK(c) do { if (!(c)) { printf(\"FAIL %s\\n\", #c); return 1; } } while (0)\n"
        "int main(void) {\n"
        "  CHECK(G2D_FIELD_GET(G2D_CSR_STATUS_KICK_FREE, G2D_FIELD_SET(G2D_CSR_STATUS_KICK_FREE, 5)) == 5u);\n"
        + "\n".join("  " + c for c in checks)
        + "\n  return 0;\n}\n"
    )
    exe = tmp_path / "t"
    subprocess.run(
        ["gcc", "-std=c99", "-Wall", "-Wextra", "-Werror", "-I", str(out / "sw/include/generated"), str(src), "-o", str(exe)],
        check=True,
    )
    subprocess.run([str(exe)], check=True)


GOOD_REG = '- {name: A, offset: 0, fields: [{name: X, bits: "0", access: rw}]}'


@pytest.mark.parametrize(
    "consts,msg",
    [
        ("  g: {prefix: CSR, values: {A: 1}}", "prefix must be"),
        ("  g: {prefix: P, values: {}}", "non-empty"),
        ("  g: {prefix: P, values: {a: 1}}", "upper-case"),
        ("  g: {prefix: P, values: {A: 1, B: 1}}", "duplicate values"),
        ("  g: {prefix: P, values: {A: 1}}\n  h: {prefix: P, values: {B: 2}}", "used twice"),
    ],
)
def test_constant_validation_errors(tmp_path, consts, msg):
    p = tmp_path / "r.yaml"
    p.write_text(MINIMAL % textwrap.indent(GOOD_REG, "      ") + "constants:\n" + consts + "\n")
    with pytest.raises(gen_regs.RegsError, match=msg):
        gen_regs.load(p)


def test_unknown_top_level_key(tmp_path):
    p = tmp_path / "r.yaml"
    p.write_text(MINIMAL % textwrap.indent(GOOD_REG, "      ") + "bogus: 1\n")
    with pytest.raises(gen_regs.RegsError, match="unknown top-level"):
        gen_regs.load(p)
