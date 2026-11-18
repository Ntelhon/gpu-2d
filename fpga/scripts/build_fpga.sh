#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Cheshire + G2D bitstream for the Genesys2 (Phase 7): build/fpga/impl/g2d_chs.genesys2.bit; with G2D_FPGA_ASYNC=1
# (v1.1) the G2D core runs on its own 100 MHz clock: build/fpga/impl_async/g2d_chs_async.genesys2.bit
# G2D_FPGA_TRIM=1 (v1.2) builds the trimmed Cheshire (no USB, iDMA, bus-error units, I2C) into impl[_async]_trim,
# bitstream g2d_chs[_async]_trim.genesys2.bit.
# Usage: build_fpga.sh <G2D RTL files, packages first>   (the Makefile target fpga passes them)
# Env: CHS_ROOT (Cheshire from setup_cheshire.sh), G2D_BOARD_REPO (Digilent vivado-boards board_files,
#      default ~/tools/src/vivado-boards/new/board_files), vivado in PATH, G2D_VIVADO_JOBS (default 8).
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/../.." && pwd)
CHS_ROOT=${CHS_ROOT:-$HOME/tools/src/cheshire}
export CHS_ROOT
export G2D_BOARD_REPO=${G2D_BOARD_REPO:-$HOME/tools/src/vivado-boards/new/board_files}
export G2D_FPGA_ASYNC=${G2D_FPGA_ASYNC:-0}
if [ "$G2D_FPGA_ASYNC" = 1 ]; then OUT=$ROOT/build/fpga/impl_async; TOP_ARGS=--async; else OUT=$ROOT/build/fpga/impl; TOP_ARGS=; fi
export G2D_FPGA_TRIM=${G2D_FPGA_TRIM:-0}
if [ "$G2D_FPGA_TRIM" = 1 ]; then OUT=${OUT}_trim; TOP_ARGS="$TOP_ARGS --trim"; fi
# per-unit trim (Q-FPGA-9): G2D_FPGA_TRIM_MASK=<hex> (bit 0 USB, 1 iDMA, 2 bus-error units, 3 I2C), own output dir
if [ -n "${G2D_FPGA_TRIM_MASK:-}" ]; then OUT=${OUT}_tm${G2D_FPGA_TRIM_MASK}; fi
# DOOM side project: chs_ps2 on the USB HID pins (bitstream g2d_chs[_async][_trim]_doom.genesys2.bit)
export G2D_FPGA_DOOM=${G2D_FPGA_DOOM:-0}
if [ "$G2D_FPGA_DOOM" = 1 ]; then OUT=${OUT}_doom; TOP_ARGS="$TOP_ARGS --ps2"; fi
XIL=$CHS_ROOT/target/xilinx
BENDER=${BENDER:-bender}
command -v vivado >/dev/null || { echo "build_fpga: vivado not in PATH (source Vivado's settings64.sh)" >&2; exit 1; }
[ -f "$G2D_BOARD_REPO/genesys2/H/board.xml" ] || {
  echo "build_fpga: Genesys2 board files not found in $G2D_BOARD_REPO" >&2; exit 1; }
mkdir -p "$OUT"

# 1. our top, generated from Cheshire's
python3 "$ROOT/fpga/scripts/gen_top.py" "$CHS_ROOT" "$OUT/g2d_chs_top_xilinx.sv" $TOP_ARGS

# 2. Cheshire's sources for Vivado (same targets as Cheshire's xilinx.mk)
(cd "$CHS_ROOT" && "$BENDER" script vivado -t fpga -t genesys2 -t rtl -t cva6 -t cv64a6_imafdchsclic_sv39_wb) \
  > "$OUT/add_sources.tcl"
# axi_vga with per-frame re-synchronisation (fpga/cheshire/axi_vga_g2d) replaces Cheshire's (DOOM board run 2026-11-05)
sed -i -E '/axi_vga[-0-9a-f]*\/src\/axi_vga(_fetcher)?\.sv/d' "$OUT/add_sources.tcl"

# 3. G2D sources
{ for f in "$@"; do echo "$ROOT/$f"; done
  echo "$ROOT/fpga/cheshire/axi_vga_g2d/axi_vga_fetcher.sv"
  echo "$ROOT/fpga/cheshire/axi_vga_g2d/axi_vga.sv"
  echo "$ROOT/fpga/cheshire/g2d_chs_pkg.sv"
  echo "$ROOT/fpga/cheshire/g2d_cheshire.sv"
  [ "$G2D_FPGA_ASYNC" = 1 ] && echo "$ROOT/fpga/cheshire/g2d_core_rst.sv"
  [ "$G2D_FPGA_DOOM" = 1 ] && echo "$ROOT/fpga/periph/chs_ps2.sv"
  echo "$OUT/g2d_chs_top_xilinx.sv"; } > "$OUT/g2d_files.f"

# 4. Cheshire's Xilinx IPs for the Genesys2 (built once inside the Cheshire checkout, as its own flow does)
ips=()
for ip in clkwiz vio mig7s; do
  d=$XIL/build/genesys2.$ip
  if [ ! -e "$d/out.xci" ]; then
    mkdir -p "$d"
    (cd "$d" && vivado -mode batch -nojournal -log ../genesys2.$ip.log \
       -source "$ROOT/fpga/genesys2/vivado_boards.tcl" -tclargs "$XIL/scripts/impl_ip.tcl" genesys2 $ip >/dev/null)
  fi
  [ "$G2D_FPGA_ASYNC" = 1 ] && [ $ip = clkwiz ] && continue   # replaced by g2d_clkwiz below
  ips+=("$(realpath "$d/out.xci")")   # the IP file itself: out.xci is a symlink (impl_sys.tcl does the same)
done
# async build: our clock wizard (Cheshire's configuration + a 100 MHz output for the G2D core), built once
if [ "$G2D_FPGA_ASYNC" = 1 ]; then
  d=$ROOT/build/fpga/ip/g2d_clkwiz
  if [ ! -e "$d/out.xci" ]; then
    mkdir -p "$ROOT/build/fpga/ip"
    (cd "$ROOT/build/fpga/ip" && vivado -mode batch -nojournal -log g2d_clkwiz.log \
       -source "$ROOT/fpga/genesys2/vivado_boards.tcl" -tclargs "$ROOT/fpga/genesys2/g2d_clkwiz_ip.tcl" "$d" >/dev/null)
  fi
  ips+=("$(realpath "$d/out.xci")")
fi

# 5. synthesis, implementation, bitstream
(cd "$OUT" && vivado -mode batch -nojournal -log vivado.log -source "$ROOT/fpga/genesys2/vivado_boards.tcl" \
   -tclargs "$ROOT/fpga/genesys2/impl_g2d.tcl" "$OUT" "$OUT/add_sources.tcl" "$OUT/g2d_files.f" "${ips[@]}" \
   >/dev/null) || true
grep -E "^G2D_IMPL_(DONE|ERROR)" "$OUT/vivado.log" || { echo "build_fpga: Vivado ended without a result line"; exit 1; }
grep -q "^G2D_IMPL_DONE" "$OUT/vivado.log"   # anchored: Vivado echoes the Tcl source with "## "
