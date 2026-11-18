# SPDX-License-Identifier: Apache-2.0
"""The full G2D with each memory variant (v1.9, B-2, verification-plan.md RAM-01): rtl/ram/<impl>/g2d_ram_1r1w.sv
and g2d_ram_sp.sv replace the behavioural rtl/common versions (as an integrator's file list does), and a subset of
test_top.py and of the path / blit tests runs bit-exact against the golden model. The ASIC variant runs on the
behavioural macro stand-ins rtl/ram/asic/models (the port mapping, bit-write masks and enables are what is
tested). The IHP SG13G2 variant (v2.0 step C, rtl/ram/ihp) runs on the PDK's own SRAM models (FUNCTIONAL view) and
is skipped when the PDK is not installed (IHP_PDK_ROOT, default ~/tools/pdk/IHP-Open-PDK; docs/TOOLS.md)."""

import os
from pathlib import Path

import pytest

IMPLS = ["xilinx", "asic", "ihp"]
IHP_SRAM = Path(os.environ.get("IHP_PDK_ROOT", Path.home() / "tools/pdk/IHP-Open-PDK")) / \
    "ihp-sg13g2/libs.ref/sg13g2_sram/verilog"


def ihp_models(srcs):
    """PDK model files of the macros the IHP tile modules name, plus their behavioural cores."""
    import re
    names = set()
    for s in srcs:
        if s.name.startswith("g2d_ihp_"):
            names |= set(re.findall(r"\b(RM_IHPSG13_\w+)\s+u_macro", s.read_text()))
    return sorted(IHP_SRAM / f"{n}.v" for n in names) + sorted(IHP_SRAM.glob("RM_IHPSG13_*_core_behavioral*.v"))


TESTS = ["fil01_all_formats", "random_streams", "csr06_soft_reset", "mem_qos_system"]


@pytest.mark.parametrize("impl", IMPLS)
def test_ram_variant(impl):
    import runner
    root = runner.ROOT
    srcs = [p for p in runner.rtl_sources() if p.name not in ("g2d_ram_1r1w.sv", "g2d_ram_sp.sv")]
    srcs += [root / "rtl/ram" / impl / "g2d_ram_1r1w.sv", root / "rtl/ram" / impl / "g2d_ram_sp.sv"]
    srcs += sorted((root / "rtl/ram" / impl / "models").glob("*.sv"))
    extra = {}
    if impl == "ihp":
        if not IHP_SRAM.is_dir():
            pytest.skip(f"IHP SG13G2 PDK not found ({IHP_SRAM})")
        srcs += [root / "rtl/ram/ihp/g2d_ihp_2p.sv", root / "rtl/ram/ihp/g2d_ihp_1p.sv"]
        srcs += ihp_models(srcs)
        extra = dict(defines={"FUNCTIONAL": 1},
                     extra_build_args=["--no-timing", str(root / "rtl/ram/ihp/ihp_models.vlt")])
    tb = root / "build/dv/tb/g2d_top_tb.sv"
    import tbwrap
    tbwrap.write(root / "rtl/top/g2d_top.sv", "g2d_top", tb.parent)
    sources = runner.PKGS + srcs + [tb]
    runner.run("g2d_top_tb", "test_top", params=dict(DATA_W=64), tag=f"ram_{impl}", testcase=TESTS,
               sources=sources, extra_env={"G2D_QUICK": "1"}, **extra)
    # random streams of every draw kind (fills, blits, paths, gradients, patterns): the blit, raster and ramp RAMs
    runner.run("g2d_top_tb", "test_sys", params=dict(DATA_W=64), tag=f"ram_{impl}", testcase=["sys01_random_streams"],
               sources=sources, extra_env={"G2D_QUICK": "1"}, **extra)


# IHP tile paths that the default g2d_top does not use (single-port macros, 48-bit / 16-bit / padded tiles, depth
# 4096, flops): the random RAM bench of test_common.py (sram_random) on the PDK models.
IHP_SIZES = [("g2d_ram_sp", 100, 256, 1), ("g2d_ram_sp", 64, 4096, 4), ("g2d_ram_sp", 32, 2000, 2),
             ("g2d_ram_sp", 24, 8, 1), ("g2d_ram_1r1w", 67, 64, 1), ("g2d_ram_1r1w", 9, 1024, 1),
             ("g2d_ram_1r1w", 512, 32, 8), ("g2d_ram_1r1w", 8, 4096, 1)]


@pytest.mark.parametrize("top,width,depth,be_w", IHP_SIZES)
def test_ihp_ram_sizes(top, width, depth, be_w):
    if not IHP_SRAM.is_dir():
        pytest.skip(f"IHP SG13G2 PDK not found ({IHP_SRAM})")
    import runner
    root = runner.ROOT
    srcs = [root / "rtl/ram/ihp" / f"{top}.sv", root / "rtl/ram/ihp/g2d_ihp_2p.sv", root / "rtl/ram/ihp/g2d_ihp_1p.sv"]
    srcs += ihp_models(srcs)
    runner.run(top, "test_common", params={"WIDTH": width, "DEPTH": depth, "BE_W": be_w},
               tag=f"ihp_W{width}_D{depth}_B{be_w}", testcase=["sram_random"], sources=srcs,
               extra_env={"G2D_DEPTH": str(depth)}, defines={"FUNCTIONAL": 1},
               extra_build_args=["--no-timing", str(root / "rtl/ram/ihp/ihp_models.vlt")])
