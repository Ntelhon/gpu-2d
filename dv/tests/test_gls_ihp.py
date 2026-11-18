# SPDX-License-Identifier: Apache-2.0
"""Gate-level simulation of g2d_top on IHP SG13G2 (v2.0 step C, docs/ASIC.md "Gate-level simulation"): the netlist
from LibreLane (post-route by default, or post-synthesis), turned into Verilator-ready Verilog by
scripts/gls_netlist.py (standard cells from their Liberty functions) with the PDK's SRAM models, runs the subset of
test_ram_variants.py bit-exact against the golden model. This is the equivalence check of the synthesized netlist
against the RTL by simulation (no formal LEC in the open flow, CLOSURE_V2 Q-TOOL-1). Zero-delay: timing is signed
off by STA, not here. The bench runs in black-box mode (g2d_env: no white-box cross-checks on a flat netlist).

Defect 0 of the v2.0 reference layout (docs/ASIC.md): sv2v 0.0.13.1 converted the index `na_q[~cur_q]` of
`g2d_raster` wrongly, so that netlist lost the path draws. Fixed in the RTL in v2.1 (`nxt_par`, checked without the
PDK by dv/tests/test_sv2v_equiv.py and here by `test_sv2v_equiv`); `test_gls_sys` stays an expected failure on the
p20e netlist, which predates the fix, until a new layout is routed.

Netlist: G2D_GLS_NETLIST, default the newest build/asic/ihp/*/runs/pnr*/final/nl/g2d_top.nl.v. Opt-in (G2D_GLS=1 or
G2D_GLS_NETLIST, as `make asic-gls` does): the netlist build takes long, and the ASIC flow is not part of `make test`.
Skipped when there is no netlist or no PDK."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from test_ram_variants import IHP_SRAM, TESTS

ROOT = Path(__file__).resolve().parents[2]


def netlist() -> Path | None:
    if os.environ.get("G2D_GLS_NETLIST"):
        return Path(os.environ["G2D_GLS_NETLIST"])
    c = sorted((ROOT / "build/asic/ihp").glob("*/runs/pnr*/final/nl/g2d_top.nl.v"), key=lambda p: p.stat().st_mtime)
    return c[-1] if c else None


SV2V_BUG = "the p20e reference netlist predates the v2.1 fix of the sv2v mis-conversion in g2d_raster (docs/ASIC.md defect 0)"


def _opt_in():
    if os.environ.get("G2D_GLS") != "1" and not os.environ.get("G2D_GLS_NETLIST"):
        pytest.skip("gate-level simulation is opt-in: make asic-gls (G2D_GLS=1)")


def _gls_setup():
    _opt_in()
    nl = netlist()
    if nl is None or not nl.exists() or not IHP_SRAM.is_dir():
        pytest.skip("no IHP gate-level netlist (scripts/asic_ihp.py) or no PDK")
    import re
    import runner
    import tbwrap
    tag = nl.parts[nl.parts.index("runs") - 1] if "runs" in nl.parts else nl.stem
    out = ROOT / "build/dv/gls" / f"{tag}_g2d_top_gls.v"
    if not out.exists() or out.stat().st_mtime < nl.stat().st_mtime:
        subprocess.run([sys.executable, str(ROOT / "scripts/gls_netlist.py"), str(nl), str(out)], check=True)
    cells = set(re.findall(r"\b(RM_IHPSG13_\w+)\s+\S+\s*\(", out.read_text()))
    models = sorted(IHP_SRAM / f"{c}.v" for c in cells) + sorted(IHP_SRAM.glob("RM_IHPSG13_*_core_behavioral*.v"))
    # the bench wrapper of the RTL, without the parameter overrides (a netlist has the default parameters built in)
    rtl_tb = tbwrap.write(ROOT / "rtl/top/g2d_top.sv", "g2d_top", ROOT / "build/dv/tb")
    tb = ROOT / "build/dv/gls/g2d_top_tb.sv"
    tb.write_text(re.sub(r"g2d_top\s*#\(.*?\)\s*u_dut\s*\(", "g2d_top u_dut (", rtl_tb.read_text(), flags=re.S))
    return runner, dict(params=dict(DATA_W=64), tag=f"gls_{tag}", sources=runner.PKGS + [out] + models + [tb],
                        extra_env={"G2D_QUICK": "1"}, defines={"FUNCTIONAL": 1}, with_sva=False,
                        extra_build_args=["--no-timing", "-Wno-fatal", str(ROOT / "rtl/ram/ihp/ihp_models.vlt"),
                                          str(ROOT / "dv/tests/gls.vlt")])


def test_gls_top():
    """Routed netlist: fills in every format, soft reset, random streams (mem_qos_system reads internal signals of
    g2d_top and cannot run on a flat netlist)."""
    runner, common = _gls_setup()
    runner.run("g2d_top_tb", "test_top", testcase=[x for x in TESTS if x != "mem_qos_system"], **common)


@pytest.mark.xfail(strict=os.environ.get("G2D_GLS_NETLIST", "").find("p20e") >= 0, reason=SV2V_BUG)
def test_gls_sys():
    """Routed netlist: random system streams with path draws."""
    runner, common = _gls_setup()
    runner.run("g2d_top_tb", "test_sys", testcase=["sys01_random_streams"], **common)


def test_sv2v_equiv():
    """The sv2v output of the RTL (what synthesis reads) on the random system streams, without any synthesis."""
    _opt_in()
    if not IHP_SRAM.is_dir():
        pytest.skip("no PDK")
    import re
    import runner
    import tbwrap
    sys.path.insert(0, str(ROOT / "scripts"))
    import asic_ihp
    v = asic_ihp.convert(ROOT / "build/dv/gls/sv2v")
    cells = set(re.findall(r"\b(RM_IHPSG13_\w+)\s+u_macro", v.read_text()))
    models = sorted(IHP_SRAM / f"{c}.v" for c in cells) + sorted(IHP_SRAM.glob("RM_IHPSG13_*_core_behavioral*.v"))
    tb = tbwrap.write(ROOT / "rtl/top/g2d_top.sv", "g2d_top", ROOT / "build/dv/tb")
    runner.run("g2d_top_tb", "test_sys", params=dict(DATA_W=64), tag="sv2v_ihp", testcase=["sys01_random_streams"],
               sources=[v] + models + [tb], extra_env={"G2D_QUICK": "1"}, defines={"FUNCTIONAL": 1}, with_sva=False,
               extra_build_args=["--no-timing", "-Wno-fatal", str(ROOT / "rtl/ram/ihp/ihp_models.vlt"),
                                 str(ROOT / "dv/tests/gls.vlt")])
