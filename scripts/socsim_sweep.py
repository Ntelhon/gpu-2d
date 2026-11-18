#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""DRAM-latency sweep on the Cheshire + G2D SoC simulation (v1.2, Q-FPGA-4 prediction).

For every scene kind, one bare-metal test binary (fpga/scripts/build_sw.sh, one scene of that kind, seed 1: the
same scenes as the Phase 7 zero-latency runs) is run on the SoC simulation with extra DRAM latency N added by the
testbench's delay stage (Cheshire's axi_fifo_delay_dyn behind the LLC, as USE_RAM_DELAY on the Genesys2; N on AR
and on AW, in 50 MHz SoC cycles). Every run is checked bit-exactly with scripts/fpga_check.py; the busy cycles of
each scene go into a Markdown table.

  socsim_sweep.py [--delays 0,10,20,40] [--kinds clears,...] [--out build/fpga/socsim_sweep] [--async] [--trim]
                  [--ps2] [--ddr [REFI,RFC,JITTER]] [-j N]

--ddr adds the testbench's DRAM refresh / jitter stage (fpga/sim/tb_dram_stall.sv, v1.8); --ps2 uses the DOOM
build of the simulation (make fpga-sim-build FPGA_DOOM=1: LLC cached region [0x8000_0000, 0x8100_0000)).
The test binaries take G2D_LLC_CACHE / G2D_FPGA_DBG from the environment (fpga/scripts/build_sw.sh).

Needs the SoC simulation built (make fpga-sim-build [FPGA_ASYNC=1]) and the bare-metal toolchain (make fpga-sw).
"""
import argparse
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KINDS = ["clears", "blits", "paths", "patterns", "converted", "strokes", "vectors", "radial", "imgpaths", "v16",
         "v16b"]


def build_elf(kind, out):
    elf = out / kind / "g2d_fpga_test.sim.elf"
    if elf.exists():
        return elf
    (out / kind).mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, FPGA_SW_OUT=str(out / kind),
               FPGA_SCENES_ARGS="--per-kind 1 --seed 1 --kinds %s" % kind, FPGA_LVGL_ARGS="--none")
    with open(out / kind / "build.log", "w") as f:
        r = subprocess.run([str(ROOT / "fpga" / "scripts" / "build_sw.sh")], env=env, stdout=f,
                           stderr=subprocess.STDOUT)
    if r.returncode:
        sys.exit("socsim_sweep.py: build of %s failed, see %s" % (kind, out / kind / "build.log"))
    return elf


def run_one(kind, delay, out, async_core, timeout_us, trim=False, ps2=False, ddr=None):
    work = out / kind / ("d%d" % delay)
    work.mkdir(parents=True, exist_ok=True)
    log = work / "uart.log"
    cmd = [sys.executable, str(ROOT / "fpga" / "sim" / "socsim.py"), "run", str(out / kind / "g2d_fpga_test.sim.elf"),
           "--work", str(work), "--log", str(log), "--timeout-us", str(timeout_us),
           "--rd-delay", str(delay), "--wr-delay", str(delay)] + (["--async"] if async_core else []) + \
          (["--trim"] if trim else []) + (["--ps2"] if ps2 else []) + (["--ddr", ddr] if ddr else [])
    with open(work / "sim.log", "w") as f:
        subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT)
    rep = work / "check.md"
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "fpga_check.py"), str(out / kind / "expected.txt"),
                        str(log), "--report", str(rep)], capture_output=True, text=True)
    rows = []
    if rep.exists():
        for ln in rep.read_text().splitlines():
            m = re.match(r"\| (\d+) \| (\S+) \| (\w+) \| (\d*) \| (\d*) \|", ln)
            if m:
                rows.append((m.group(2), m.group(3), m.group(4), m.group(5)))
    return kind, delay, r.returncode == 0, rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--delays", default="0,10,20,40")
    ap.add_argument("--kinds", default=",".join(KINDS))
    ap.add_argument("--out", default=str(ROOT / "build" / "fpga" / "socsim_sweep"))
    ap.add_argument("--async", dest="async_core", action="store_true")
    ap.add_argument("--trim", action="store_true", help="trimmed Cheshire build (make fpga-sim-build FPGA_TRIM=1)")
    ap.add_argument("--ps2", action="store_true", help="DOOM build of the simulation (make fpga-sim-build FPGA_DOOM=1)")
    ap.add_argument("--ddr", nargs="?", const="390,13,32", metavar="REFI,RFC,JITTER",
                    help="DRAM refresh / jitter stage (socsim.py run --ddr)")
    ap.add_argument("--timeout-us", type=int, default=6000000, help="simulated time limit per run")
    ap.add_argument("-j", "--jobs", type=int, default=int(os.environ.get("JOBS", "8")))
    a = ap.parse_args()
    out = Path(a.out).resolve()
    kinds = a.kinds.split(",")
    delays = [int(d) for d in a.delays.split(",")]
    for k in kinds:
        build_elf(k, out)
    jobs = [(k, d) for k in kinds for d in delays]
    with ThreadPoolExecutor(max_workers=min(a.jobs, 8)) as ex:
        res = list(ex.map(lambda kd: run_one(kd[0], kd[1], out, a.async_core, a.timeout_us, a.trim, a.ps2, a.ddr), jobs))

    busy = {(k, d): rows for k, d, _, rows in res}
    ok_all = all(ok for _, _, ok, _ in res)
    lines = ["# SoC simulation DRAM-latency sweep%s%s" % (" (async build)" if a.async_core else "",
                                                    " (trimmed Cheshire)" if a.trim else "") + (" (DOOM build)" if a.ps2 else "") +
             (" (DRAM stage %s)" % a.ddr if a.ddr else ""), "",
             "Extra DRAM latency N on AR and AW (SoC cycles, 50 MHz) behind the LLC; busy cycles per scene "
             "(PERF_BUSY_CYCLES); every run bit-exact unless marked.", "",
             "| scene | " + " | ".join("N=%d" % d for d in delays) + " |",
             "|---|" + "---|" * len(delays)]
    for k in kinds:
        names = [r[0] for r in busy.get((k, delays[0]), [])] or [k]
        for i, name in enumerate(names):
            cells = []
            for d in delays:
                rows = busy.get((k, d), [])
                if i < len(rows):
                    cells.append(rows[i][2] + ("" if rows[i][1] == "PASS" else " (%s)" % rows[i][1]))
                else:
                    cells.append("no result")
            lines.append("| %s | %s |" % (name, " | ".join(cells)))
    lines += ["", "result: **%s**" % ("PASS" if ok_all else "FAIL")]
    (out / "sweep.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    sys.exit(0 if ok_all else 1)


if __name__ == "__main__":
    main()
