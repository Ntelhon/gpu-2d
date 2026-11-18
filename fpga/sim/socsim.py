#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Cheshire + G2D SoC simulation on Verilator (Phase 7).

  socsim.py build --rtl <file> ...           verilate tb_g2d_chs into build/fpga/socsim
  socsim.py run <prog.elf> [--baud N] [--timeout-us N] [--log <uart.log>] [--work <dir>]
                 [--rd-delay N] [--wr-delay N]     extra DRAM latency (v1.2, cfg registers as on the board)
                 [--vga-capture <file>]            first full VGA frame from the pins, RGB565 (v1.2)

Needs verilator (>= 5.036, scripts/env.sh), bender and a Cheshire checkout set up by
fpga/scripts/setup_cheshire.sh (CHS_ROOT, default ~/tools/src/cheshire). Questa / VCS (Cheshire's own
simulators) are not used, and Vivado xsim 2024.2 crashes elaborating cheshire_soc
(docs/OPEN_QUESTIONS.md Q-FPGA-7).
"""
import argparse
import os
import shlex
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BUILD = ROOT / "build" / "fpga" / "socsim"
CHS_ROOT = Path(os.environ.get("CHS_ROOT", Path.home() / "tools" / "src" / "cheshire"))
BENDER = os.environ.get("BENDER", "bender")
JOBS = os.environ.get("JOBS", "8")   # project limit: 8 cores
# Same targets as Cheshire's own simulation flow (cheshire.mk CHS_BENDER_RTL_FLAGS), without its
# testbench (targets sim/test pull vendor flash / EEPROM models that are not used here).
CHS_TARGETS = ["rtl", "cva6", "cv64a6_imafdchsclic_sv39_wb"]
# Listed by the dependencies but not instantiated, and not Verilator-compatible (gate primitives).
CHS_SKIP = ("pad_functional.sv",)
# axi_vga with per-frame re-synchronisation replaces Cheshire's (2026-11-05, DOOM board run: the picture slid)
VGA_ORIG = ("/axi_vga.sv", "/axi_vga_fetcher.sv")
VGA_G2D = ["fpga/cheshire/axi_vga_g2d/axi_vga_fetcher.sv", "fpga/cheshire/axi_vga_g2d/axi_vga.sv"]
# v1.8: the fork's R buffer is our block-RAM FIFO; the SoC / FPGA builds compile these with the G2D RTL anyway, a
# standalone bench of the fork needs them added
VGA_G2D_DEPS = ["rtl/common/g2d_ram_1r1w.sv", "rtl/common/g2d_fifo.sv", "rtl/common/g2d_fifo_ram.sv"]
FPGA_SRCS = VGA_G2D + ["fpga/cheshire/g2d_chs_pkg.sv", "fpga/cheshire/g2d_cheshire.sv", "fpga/periph/chs_ps2.sv",
             "fpga/sim/tb_dram_stall.sv", "fpga/sim/tb_g2d_chs.sv"]
TOP = "tb_g2d_chs"
VFLAGS = ["--binary", "--timing", "-O3", "--x-assign", "fast", "--x-initial", "fast",
          "-Wno-fatal", "-Wno-lint", "-Wno-style", "-Wno-ZEROREPL", "-Wno-UNOPTFLAT", "-Wno-MULTIDRIVEN",
          "-Wno-TIMESCALEMOD", "-Wno-SELRANGE", "-Wno-ZERODLY", "-Wno-SYMRSVDWORD"]


def sh(cmd, cwd=None, log=None):
    print("+", " ".join(shlex.quote(str(c)) for c in cmd), flush=True)
    with open(log, "w") if log else open(os.devnull, "w") as f:
        r = subprocess.run([str(c) for c in cmd], cwd=cwd, stdout=f if log else None,
                           stderr=subprocess.STDOUT if log else None)
    if r.returncode:
        sys.exit("socsim.py: command failed (%d)%s" % (r.returncode, ", log " + str(log) if log else ""))


def cheshire_flist(orig_vga=False):
    out = subprocess.run([BENDER, "script", "flist-plus"] + sum((["-t", t] for t in CHS_TARGETS), []),
                         cwd=CHS_ROOT, check=True, capture_output=True, text=True).stdout
    args = []
    for line in out.split():
        if line.endswith((".svh", ".vh")):
            continue                     # headers listed as sources are `include'd where needed
        if line.endswith(CHS_SKIP) or (line.endswith(VGA_ORIG) and not orig_vga):
            continue
        args.append(line)
    axi = subprocess.run([BENDER, "path", "axi"], cwd=CHS_ROOT, check=True, capture_output=True,
                         text=True).stdout.strip()
    args.append(os.path.join(axi, "src", "axi_sim_mem.sv"))
    # v1.2: the Genesys2 top's cfg registers (DRAM delay stage), Cheshire target/xilinx (bender target xilinx).
    args += [str(CHS_ROOT / "target" / "xilinx" / "src" / "regs" / f)
             for f in ("chs_xilinx_reg_pkg.sv", "chs_xilinx_reg_top.sv")]
    if not any(a.endswith("axi_fifo_delay_dyn.sv") for a in args):
        args.append(os.path.join(axi, "src", "axi_fifo_delay_dyn.sv"))
    return args


def obj_name(args):
    return "obj" + ("_async" if args.async_core else "") + ("_trim" if args.trim else "") + ("_ps2" if args.ps2 else "")


def build(args):
    if not (CHS_ROOT / "Bender.yml").exists():
        sys.exit("socsim.py: no Cheshire at %s (run fpga/scripts/setup_cheshire.sh)" % CHS_ROOT)
    BUILD.mkdir(parents=True, exist_ok=True)
    flist = BUILD / "files.f"
    flist.write_text("\n".join(cheshire_flist() + [str(ROOT / f) for f in args.rtl + FPGA_SRCS]) + "\n")
    obj = BUILD / obj_name(args)
    sh(["verilator"] + VFLAGS + ["-j", JOBS, "-f", flist, "--top-module", TOP, "-Mdir", obj, "-o", TOP] +
       (["-GCoreAsync=1"] if args.async_core else []) + (["-GTrim=1"] if args.trim else []) +
       (["-GPs2=1"] if args.ps2 else []),
       cwd=BUILD, log=BUILD / ("verilator%s.log" % obj_name(args)[3:]))
    print("socsim.py: built %s" % (obj / TOP))


def run(args):
    elf = Path(args.elf).resolve()
    work = Path(args.work).resolve() if args.work else BUILD / ("run_" + elf.stem)
    work.mkdir(parents=True, exist_ok=True)
    memh = work / "prog.memh"
    # CVA6's Verilator mock tracer writes every committed instruction to trace_hart_0.dasm in the working
    # directory (cva6.sv, `ifdef VERILATOR`); send it to /dev/null unless G2D_SOCSIM_TRACE=1.
    trace = work / "trace_hart_0.dasm"
    if trace.exists() or trace.is_symlink():
        trace.unlink()
    if os.environ.get("G2D_SOCSIM_TRACE") != "1":
        trace.symlink_to(os.devnull)
    entry = subprocess.run([sys.executable, str(ROOT / "fpga" / "sim" / "elf2memh.py"), str(elf), str(memh)],
                           check=True, capture_output=True, text=True).stdout.strip()
    log = Path(args.log).resolve() if args.log else work / "uart.log"
    cmd = [str(BUILD / obj_name(args) / TOP), "+MEMH=%s" % memh, "+ENTRY=%s" % entry, "+UART_BAUD=%d" % args.baud,
           "+TIMEOUT_US=%d" % args.timeout_us, "+UART_LOG=%s" % log]
    if args.rd_delay is not None:
        cmd.append("+RD_DELAY=%d" % args.rd_delay)
    if args.wr_delay is not None:
        cmd.append("+WR_DELAY=%d" % args.wr_delay)
    if args.ddr:
        refi, rfc, jit = (int(x) for x in args.ddr.split(","))
        cmd += ["+DRAM_REFI=%d" % refi, "+DRAM_RFC=%d" % rfc, "+DRAM_JITTER=%d" % jit, "+DRAM_SEED=%d" % args.ddr_seed]
    if args.vga_capture:
        cmd.append("+VGA_CAPTURE=%s" % Path(args.vga_capture).resolve())
    if args.vga_trigger:
        cmd.append("+VGA_TRIGGER=%s" % args.vga_trigger)
    if args.bin:
        f, a = args.bin.rsplit("@", 1)
        cmd.append("+BIN=%s@%s" % (Path(f).resolve(), a))
    if args.ps2_keys:
        cmd.append("+PS2_KEYS=%s" % Path(args.ps2_keys).resolve())
    print("+", " ".join(cmd), flush=True)
    with open(work / "sim.out", "w") as f:
        r = subprocess.run(cmd, cwd=work, stdout=f, stderr=subprocess.STDOUT)
    out = (work / "sim.out").read_text()
    print(out[-3000:])
    ok = r.returncode == 0 and "TB_RESULT PASS" in out
    print("socsim.py: %s (%s)" % ("PASS" if ok else "FAIL", work / "sim.out"))
    sys.exit(0 if ok else 1)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--rtl", nargs="*", default=[], help="G2D RTL files (packages first), repo-relative")
    r = sub.add_parser("run")
    r.add_argument("elf")
    r.add_argument("--baud", type=int, default=1562500, help="UART rate the program was built for")
    r.add_argument("--timeout-us", type=int, default=200000, help="simulated time limit")
    r.add_argument("--log")
    r.add_argument("--work", help="run directory (default build/fpga/socsim/run_<elf>)")
    r.add_argument("--rd-delay", type=int, help="extra DRAM AR delay in SoC cycles (cfg register +0x18)")
    r.add_argument("--wr-delay", type=int, help="extra DRAM AW delay in SoC cycles (cfg register +0x0C)")
    r.add_argument("--ddr", nargs="?", const="390,13,32", metavar="REFI,RFC,JITTER",
                   help="v1.8: DDR-like DRAM timing (fpga/sim/tb_dram_stall.sv): refresh RFC of every REFI cycles and "
                        "JITTER/256 random stall probability per cycle (default 390,13,32)")
    r.add_argument("--ddr-seed", type=int, default=1, help="seed of the --ddr jitter")
    r.add_argument("--vga-capture", help="write the first full VGA frame (RGB565 from the pins) to this file")
    r.add_argument("--vga-trigger", help="hex address: capture only after the program wrote a non-zero byte there")
    r.add_argument("--bin", help="<file>@<hex addr>: binary image preloaded into DRAM (DOOM: the WAD image)")
    r.add_argument("--ps2-keys", help="PS/2 bytes to send, lines '<time_us> <hex byte>' (build with --ps2)")
    for p in (b, r):
        p.add_argument("--async", dest="async_core", action="store_true",
                       help="v1.1 async build: G2D core on its own 100 MHz clock, ports through the CDC")
        p.add_argument("--trim", action="store_true",
                       help="v1.2 trimmed Cheshire: no USB, iDMA, bus-error units, I2C")
        p.add_argument("--ps2", action="store_true", help="DOOM: chs_ps2 on the second external register slave")
    a = ap.parse_args()
    build(a) if a.cmd == "build" else run(a)


if __name__ == "__main__":
    main()
