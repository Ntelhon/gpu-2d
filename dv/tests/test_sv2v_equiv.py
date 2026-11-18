# SPDX-License-Identifier: Apache-2.0
"""sv2v conversion check (v2.1, docs/ASIC.md defect 0): the RTL converted by sv2v (what Yosys reads in the ASIC flow,
scripts/asic_ihp.py) runs the same benches as the RTL against the golden model, with the behavioural RAMs (no PDK).
Skipped without sv2v.

Found by the gate-level simulation of the v2.0 reference layout: sv2v 0.0.13.1 turned the index `na_q[~cur_q]` (1-bit
cur_q) into `~cur_q * (EI + 1)`; Verilog widens cur_q to 32 bits before the NOT, so the index went out of range and
the rasterizer produced no coverage. Fixed in the RTL (g2d_raster: `nxt_par`). Coding rule for sv2v (docs/ASIC.md):
no bitwise NOT of a narrow signal inside an index or a part-select; give it its own signal.

Cases: g2d_raster on its unit bench (16 s), g2d_top on fills / soft reset / random streams and on the random system
streams with path draws."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def sv2v_bin():
    p = Path.home() / "tools/nix-profiles/sv2v/bin/sv2v"
    return str(p) if p.exists() else shutil.which("sv2v")


def convert(top: str, params: dict) -> Path:
    import runner
    out = ROOT / "build/dv/sv2v_unit" / f"{top}.v"
    out.parent.mkdir(parents=True, exist_ok=True)
    srcs = [str(p) for p in runner.PKGS + runner.rtl_sources()]
    r = subprocess.run([sv2v_bin(), "-DSYNTHESIS", f"--top={top}", *srcs], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    out.write_text(r.stdout)
    return out


VLT = ["-Wno-fatal", str(ROOT / "dv/tests/gls.vlt")]


@pytest.mark.parametrize("top,module,params,testcase,env", [
    ("g2d_raster", "test_raster", {}, None, {}),
])
def test_sv2v_unit(top, module, params, testcase, env):
    if not sv2v_bin():
        pytest.skip("sv2v not installed (docs/TOOLS.md)")
    import runner
    v = convert(top, params)
    runner.run(top, module, params=params, tag="sv2v", testcase=testcase, sources=[v], with_sva=False,
               extra_env=env, extra_build_args=VLT)


def test_sv2v_top():
    """The whole g2d_top as sv2v converts it: top-level fills / soft reset / random streams and the random system
    streams (paths, gradients, blits) bit-exact against the model."""
    if not sv2v_bin():
        pytest.skip("sv2v not installed (docs/TOOLS.md)")
    import runner
    import tbwrap
    v = convert("g2d_top", {})
    tb = tbwrap.write(ROOT / "rtl/top/g2d_top.sv", "g2d_top", ROOT / "build/dv/tb")
    common = dict(params=dict(DATA_W=64), tag="sv2v", sources=[v, tb], extra_env={"G2D_QUICK": "1"},
                  with_sva=False, extra_build_args=VLT)
    runner.run("g2d_top_tb", "test_top", testcase=["fil01_all_formats", "csr06_soft_reset", "random_streams"], **common)
    runner.run("g2d_top_tb", "test_sys", testcase=["sys01_random_streams"], **common)
