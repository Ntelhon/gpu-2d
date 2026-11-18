#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Run the G2D bare-metal test on a Genesys2 with the Cheshire + G2D bitstream (Phase 7). Runs on the machine the
# board is connected to, from the unpacked board package (make fpga-package). Writes everything to ./results/.
#
#   ./run_board.sh [--async] [--no-program] [--uart /dev/ttyUSBx] [--timeout 900] [--kill-hw-server] [--jtag-khz 8000]
#                [--gdb]
#
# --async (v1.1): the bitstream with G2D's core on its own 100 MHz clock (g2d_chs_async.genesys2.bit); the results go
# to ./results_async/ so that both builds can be run one after the other.
#
# Needs: vivado (only to program; or program yourself with --no-program, e.g. openFPGALoader -b genesys2),
# openocd >= 0.12, python3, stty. OpenOCD (openocd/openocd_g2d.tcl) loads the program image over JTAG in 256 KB
# chunks, checks each with a CRC computed on the board (reloads bad ones), sets pc to the entry point, resumes and
# exits; no GDB and no GDB port. With --gdb, riscv64-unknown-elf-gdb loads the ELF through OpenOCD's GDB port 3333
# instead (as up to v1.2). --jtag-khz lowers the JTAG clock if the load fails (e.g. USB through a VM). Results are
# also packed into results.zip (results_async.zip). Board switches: SW1..SW0 = 0 (boot mode 0, passive),
# SW5..SW2 not all 0 (fan), SW7 = 0 (test mode). Press the CPU reset button (or power-cycle) after programming
# if the UART stays silent.
#
# v1.2: the test also replays an LVGL frame, shows it on the VGA port for 5 s (connect a monitor to see it; it stays
# on after the test) and runs a DRAM-delay sweep (Cheshire's USE_RAM_DELAY registers). Vivado's hw_server holds the
# USB-JTAG after programming; the script stops it before OpenOCD starts if it was not running before (an hw_server
# that was already running, e.g. a Vivado GUI's, is left alone unless --kill-hw-server). Everything about the host
# (USB devices, serial ports, tool versions) goes to results/env.txt.
set -uo pipefail
cd "$(dirname "$0")"

PROGRAM=1
UART=""
TIMEOUT=900
KILL_HWS=0
USE_GDB=0
JTAG_KHZ=8000
BIT=g2d_chs.genesys2.bit
RES=results
while [ $# -gt 0 ]; do
  case "$1" in
    --async) BIT=g2d_chs_async.genesys2.bit; RES=results_async ;;
    --no-program) PROGRAM=0 ;;
    --uart) UART=$2; shift ;;
    --timeout) TIMEOUT=$2; shift ;;
    --kill-hw-server) KILL_HWS=1 ;;
    --gdb) USE_GDB=1 ;;
    --jtag-khz) JTAG_KHZ=$2; shift ;;
    *) echo "unknown option $1"; exit 2 ;;
  esac
  shift
done
# v1.7: a package built with FPGA_TRIM=1 holds the trimmed Cheshire bitstreams (g2d_chs[_async]_trim.genesys2.bit)
[ -f "$BIT" ] || [ ! -f "${BIT%.genesys2.bit}_trim.genesys2.bit" ] || BIT=${BIT%.genesys2.bit}_trim.genesys2.bit
OPENOCD=${OPENOCD:-openocd}
GDB=${GDB:-riscv64-unknown-elf-gdb}
mkdir -p "$RES"
LOG=$RES/uart.log
: > "$LOG"

step() { echo "== $*" | tee -a $RES/run.txt; }
{ echo "date: $(date -Iseconds)"; echo "host: $(uname -a)"; echo "bitstream: $BIT"; } > $RES/run.txt
TOOLS="$OPENOCD python3 stty"; [ $USE_GDB = 1 ] && TOOLS="$TOOLS $GDB"
for t in $TOOLS; do
  command -v "$t" >/dev/null || { echo "missing tool: $t" | tee -a $RES/run.txt; exit 2; }
done
{
  echo "## lsusb"; lsusb 2>&1
  echo "## /dev/serial/by-id"; ls -l /dev/serial/by-id 2>&1
  echo "## ttyUSB"; ls -l /dev/ttyUSB* 2>&1
  echo "## groups"; id 2>&1
  echo "## openocd"; "$OPENOCD" --version 2>&1 | head -2
  [ $USE_GDB = 1 ] && { echo "## gdb"; "$GDB" --version 2>&1 | head -1; }
  echo "## vivado"; command -v vivado >/dev/null && vivado -version 2>&1 | head -1
  echo "## hw_server before"; pgrep -a hw_server 2>&1
  echo "## openocd before"; pgrep -a openocd 2>&1
} > $RES/env.txt 2>&1

recovery() {
  cat <<'TXT' | tee -a $RES/run.txt
Recovery (Cheshire docs/tg/xilinx.md: resetting during a debug session is not supported):
  1. this script has stopped GDB and OpenOCD (check: pgrep -a openocd);
  2. press CPU_RESET on the Genesys2 (or power-cycle and re-run without --no-program);
  3. re-run ./run_board.sh --no-program (add --async if this was the async bitstream).
If OpenOCD cannot open the adapter: another program holds the USB-JTAG (Vivado hw_server, a GUI): close it or run with
--kill-hw-server. If the UART stays silent: check SW1..SW0 = 0 (passive boot) and the --uart port (results/env.txt).
TXT
}

if [ $PROGRAM = 1 ]; then
  step "program FPGA (Vivado hardware manager)"
  command -v vivado >/dev/null || { echo "vivado not in PATH: program the bitstream yourself and use --no-program"; exit 2; }
  hws_before=$(pgrep -x hw_server)
  vivado -mode batch -nojournal -log $RES/program.log -source program.tcl -tclargs "$BIT" \
    >/dev/null 2>&1 || true
  grep -q G2D_PROGRAM_DONE $RES/program.log || { echo "programming failed, see $RES/program.log"; exit 1; }
  sleep 2
fi
# OpenOCD needs the USB-JTAG that hw_server keeps open.
if pgrep -x hw_server >/dev/null; then
  if [ -z "${hws_before:-}" ] && [ $PROGRAM = 1 ] || [ $KILL_HWS = 1 ]; then
    step "stop hw_server (holds the USB-JTAG)"
    pkill -x hw_server; sleep 2
  else
    step "hw_server is running (not started by this script): OpenOCD may fail; close it or use --kill-hw-server"
  fi
fi
{ echo "## hw_server before OpenOCD"; pgrep -a hw_server; } >> $RES/env.txt 2>&1
# An OpenOCD left over from an earlier (interrupted) run holds the USB-JTAG.
if pgrep -x openocd >/dev/null; then
  echo "an OpenOCD is already running (it holds the USB-JTAG): stop it first, e.g. pkill -x openocd"
  pgrep -a openocd; exit 2
fi

if [ -z "$UART" ]; then
  # Use the serial port only when there is exactly one; otherwise ask (ls -l /dev/serial/by-id shows which is which).
  ports=$(ls /dev/ttyUSB* 2>/dev/null)
  if [ "$(echo "$ports" | grep -c .)" = 1 ]; then UART=$ports; else
    echo "serial ports: ${ports:-none}; pass --uart /dev/ttyUSBx (the Genesys2 UART port)"; ls -l /dev/serial/by-id 2>/dev/null
    exit 2
  fi
fi
[ -e "$UART" ] || { echo "no UART device found, pass --uart /dev/ttyUSBx"; exit 2; }
step "UART $UART at 115200 8N1, log $LOG"
stty -F "$UART" 115200 cs8 -cstopb -parenb raw -echo -crtscts || exit 2
cat "$UART" >> "$LOG" &
CAT_PID=$!

ELF=g2d_fpga_test.board.elf
BIN=g2d_fpga_test.board.bin
OCD_CFG=openocd/openocd_g2d.tcl
GDB_PID=
OCD_PID=
if [ $USE_GDB = 1 ]; then
  step "OpenOCD ($OCD_CFG, JTAG $JTAG_KHZ kHz, GDB port 3333)"
  "$OPENOCD" -c "set JTAG_KHZ $JTAG_KHZ" -c "set GDB_PORT 3333" -f $OCD_CFG > $RES/openocd.log 2>&1 &
  OCD_PID=$!
  for _ in $(seq 1 30); do grep -q "Ready for Remote Connections" $RES/openocd.log && break; sleep 1; done
  grep -q "Ready for Remote Connections" $RES/openocd.log || {
    echo "OpenOCD did not connect, see $RES/openocd.log"; kill $CAT_PID $OCD_PID 2>/dev/null; recovery; exit 1; }
  step "load and run $ELF with GDB"
  "$GDB" -batch -ex "target extended-remote localhost:3333" -ex "load" -ex "continue" $ELF > $RES/gdb.log 2>&1 &
  GDB_PID=$!
else
  # The image is the ELF's one LOAD segment as a flat binary (fpga-package); it starts at the entry point.
  ENTRY=$(python3 -c 'import struct,sys; d=open(sys.argv[1],"rb").read(32); print(hex(struct.unpack_from("<Q",d,24)[0]))' $ELF)
  step "load $BIN at $ENTRY with OpenOCD ($OCD_CFG, JTAG $JTAG_KHZ kHz, CRC-checked chunks), start"
  timeout 1800 "$OPENOCD" -c "set JTAG_KHZ $JTAG_KHZ" -f $OCD_CFG \
    -c "load_verified $BIN $ENTRY $RES/chunks" -c "reg pc $ENTRY" -c "resume" -c "shutdown" > $RES/openocd.log 2>&1
  ocd_rc=$?
  grep "load_verified:" $RES/openocd.log | tee -a $RES/run.txt
  [ $ocd_rc = 0 ] && grep -q "load_verified: .* 0 bad" $RES/openocd.log || {
    echo "OpenOCD load failed (rc $ocd_rc), see $RES/openocd.log"; kill $CAT_PID 2>/dev/null; recovery; exit 1; }
  step "running"
fi

end=$((SECONDS + TIMEOUT))
while [ $SECONDS -lt $end ]; do
  grep -q "G2D_TEST_END" "$LOG" && break
  sleep 2
done
sleep 2
kill $GDB_PID $OCD_PID $CAT_PID 2>/dev/null
wait 2>/dev/null

if grep -q "G2D_TEST_END" "$LOG"; then step "test finished"; else step "TIMEOUT after ${TIMEOUT}s"; recovery; fi
step "check against expected.txt"
python3 fpga_check.py expected.txt "$LOG" --report $RES/check.md
rc=$?
step "result: $([ $rc = 0 ] && echo PASS || echo FAIL) (send back $RES.zip)"
python3 -c 'import shutil,sys; shutil.make_archive(sys.argv[1], "zip", sys.argv[1])' "$RES"
exit $rc
