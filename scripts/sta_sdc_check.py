#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Read syn/constraints/g2d_top.sdc in OpenSTA against a port-only stub of g2d_top (v1.9, D-2, OPEN_QUESTIONS
Q-INT-2). Checks for every clock configuration (CFG_ASYNC / MEM_ASYNC = 00, 10, 01, 11) that OpenSTA accepts every
command, that the expected clocks exist and that every port has its input / output delay (except the port clocks a
single-clock build leaves unused). No timing: the stub has no logic (that needs a synthesized netlist, Phase 8).

  sta_sdc_check.py [--sta PATH] [--liberty FILE]
Defaults: $G2D_STA or ~/tools/opensta/bin/sta; $G2D_LIBERTY or the IHP SG13G2 typical library under ~/tools/pdk.
Exit 0 = pass, 1 = fail, 2 = tool or library missing.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

DEF_STA = Path(os.environ.get("G2D_STA", Path.home() / "tools/opensta/bin/sta"))
DEF_LIB = Path(os.environ.get("G2D_LIBERTY", Path.home() /
               "tools/pdk/IHP-Open-PDK/ihp-sg13g2/libs.ref/sg13g2_stdcell/lib/sg13g2_stdcell_typ_1p20V_25C.lib"))


def stub(path: Path):
    import gen_packaging
    ports = gen_packaging.ports()
    o = ["module g2d_top (" + ", ".join(n for _, n, _, _ in ports) + ");"]
    for d, n, hi, lo in ports:
        o.append(f"  {d} {f'[{hi}:{lo}] ' if hi is not None else ''}{n};")
    o.append("endmodule")
    path.write_text("\n".join(o) + "\n")


def run(sta: Path, lib: Path, cfg_async: int, mem_async: int, tmp: Path) -> list[str]:
    tcl = tmp / "run.tcl"
    tcl.write_text(f"""read_liberty {lib}
read_verilog {tmp / 'g2d_top_stub.v'}
link_design g2d_top
set G2D_CFG_ASYNC {cfg_async}
set G2D_MEM_ASYNC {mem_async}
read_sdc {ROOT / 'syn/constraints/g2d_top.sdc'}
report_clock_properties
check_setup -verbose
puts G2D_SDC_DONE
""")
    out = subprocess.run([str(sta), "-no_splash", "-exit", str(tcl)], capture_output=True, text=True).stdout
    errs = []
    if "G2D_SDC_DONE" not in out or re.search(r"^(Error|error)", out, re.M):
        errs.append("OpenSTA did not read the SDC:\n" + out[-2000:])
    clocks = set(re.findall(r"^(g2d_\w+)\s+[\d.]+", out, re.M))
    want = {"g2d_core"} | ({"g2d_cfg"} if cfg_async else set()) | ({"g2d_mem"} if mem_async else set())
    if clocks != want:
        errs.append(f"clocks {sorted(clocks)}, expected {sorted(want)}")
    # ports listed under "missing set_input_delay" / "missing set_output_delay"
    unused = set() if cfg_async else {"clk_cfg_i"}
    unused |= set() if mem_async else {"clk_mem_i"}
    for kind in ("input", "output"):
        m = re.search(rf"ports missing set_{kind}_delay\.\n((?:\s+\S+\n)+)", out)
        missing = set(m.group(1).split()) if m else set()
        if missing - unused:
            errs.append(f"{kind} ports without delay: {sorted(missing - unused)[:8]}")
    return errs


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sta", type=Path, default=DEF_STA)
    ap.add_argument("--liberty", type=Path, default=DEF_LIB)
    a = ap.parse_args(argv)
    if not a.sta.exists() or not a.liberty.exists():
        print(f"sta_sdc_check: missing {a.sta if not a.sta.exists() else a.liberty}")
        return 2
    bad = 0
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        stub(tmp / "g2d_top_stub.v")
        for c, m in ((0, 0), (1, 0), (0, 1), (1, 1)):
            errs = run(a.sta, a.liberty, c, m, tmp)
            print(f"sta_sdc_check: CFG_ASYNC={c} MEM_ASYNC={m}: {'OK' if not errs else 'FAIL'}")
            for e in errs:
                print("  " + e)
            bad |= bool(errs)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
