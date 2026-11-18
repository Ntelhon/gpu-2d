#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Play DOOM on the Genesys2 (DOOM side project, step D6): Cheshire + G2D + chs_ps2 bitstream, bare-metal DOOM, a USB
# keyboard in the board's "USB HID" port, a VGA monitor on the VGA port. Runs on the machine the board is connected
# to, from the unpacked DOOM package (make doom-package). Writes logs to ./results_doom/.
#
#   ./run_doom.sh [--no-program] [--uart /dev/ttyUSBx] [--jtag-khz 8000] [--kill-hw-server] [--no-term]
#
# Steps: program the FPGA (Vivado hardware manager), load doom.board.bin (the program) and doom_wad.bin (the WAD
# with a 16-byte header, at 0xA000_0000) over JTAG with OpenOCD in CRC-checked chunks, start the program, then attach
# a terminal to the UART (log + keys; quit with Ctrl-]). The UART shows DOOM's start messages, the LLC setting and the
# frame rate. Keys: the USB keyboard (Ctrl fire, Space use, Shift run, Alt strafe, arrows, Esc menu, Enter, F1..F12,
# 1..7 weapons, , . strafe); without a keyboard the terminal's keys work too (w a s d / arrows, f fire, e use,
# Enter, Esc; a key is released 150 ms after its last repeat).
# Board switches: SW1..SW0 = 0 (passive boot), SW5..SW2 not all 0 (fan), SW7 = 0. If the UART stays silent, press
# CPU_RESET and re-run with --no-program.
set -uo pipefail
cd "$(dirname "$0")"

PROGRAM=1; UART=""; JTAG_KHZ=8000; KILL_HWS=0; TERM_ON=1
while [ $# -gt 0 ]; do
  case "$1" in
    --no-program) PROGRAM=0 ;;
    --uart) UART=$2; shift ;;
    --jtag-khz) JTAG_KHZ=$2; shift ;;
    --kill-hw-server) KILL_HWS=1 ;;
    --no-term) TERM_ON=0 ;;
    *) echo "unknown option $1"; exit 2 ;;
  esac
  shift
done
BIT=$(ls g2d_chs*_doom.genesys2.bit 2>/dev/null | head -1)
OPENOCD=${OPENOCD:-openocd}
RES=results_doom
mkdir -p "$RES"
LOG=$RES/uart.log
: > "$LOG"
step() { echo "== $*" | tee -a $RES/run.txt; }
{ echo "date: $(date -Iseconds)"; echo "host: $(uname -a)"; echo "bitstream: $BIT"; } > $RES/run.txt
for t in $OPENOCD python3 stty; do
  command -v "$t" >/dev/null || { echo "missing tool: $t" | tee -a $RES/run.txt; exit 2; }
done
[ -n "$BIT" ] || { echo "no *_doom bitstream in this directory"; exit 2; }

if [ $PROGRAM = 1 ]; then
  step "program FPGA with $BIT"
  command -v vivado >/dev/null || { echo "vivado not in PATH: program the bitstream yourself and use --no-program"; exit 2; }
  hws_before=$(pgrep -x hw_server)
  vivado -mode batch -nojournal -log $RES/program.log -source program.tcl -tclargs "$BIT" >/dev/null 2>&1 || true
  grep -q G2D_PROGRAM_DONE $RES/program.log || { echo "programming failed, see $RES/program.log"; exit 1; }
  sleep 2
fi
if pgrep -x hw_server >/dev/null; then
  if [ -z "${hws_before:-}" ] && [ $PROGRAM = 1 ] || [ $KILL_HWS = 1 ]; then
    step "stop hw_server (holds the USB-JTAG)"; pkill -x hw_server; sleep 2
  else
    step "hw_server is running (not started by this script): OpenOCD may fail; close it or use --kill-hw-server"
  fi
fi
if pgrep -x openocd >/dev/null; then
  echo "an OpenOCD is already running (it holds the USB-JTAG): stop it first, e.g. pkill -x openocd"; exit 2
fi
if [ -z "$UART" ]; then
  ports=$(ls /dev/ttyUSB* 2>/dev/null)
  if [ "$(echo "$ports" | grep -c .)" = 1 ]; then UART=$ports; else
    echo "serial ports: ${ports:-none}; pass --uart /dev/ttyUSBx (the Genesys2 UART port)"; exit 2
  fi
fi
step "UART $UART at 115200 8N1, log $LOG"
stty -F "$UART" 115200 cs8 -cstopb -parenb raw -echo -crtscts || exit 2

ENTRY=$(python3 -c 'import struct,sys; d=open(sys.argv[1],"rb").read(32); print(hex(struct.unpack_from("<Q",d,24)[0]))' doom.board.elf)
step "load doom.board.bin at $ENTRY and doom_wad.bin ($(stat -c %s doom_wad.bin) bytes) at 0xA0000000 (JTAG $JTAG_KHZ kHz)"
timeout 3600 "$OPENOCD" -c "set JTAG_KHZ $JTAG_KHZ" -f openocd/openocd_g2d.tcl \
  -c "load_verified doom.board.bin $ENTRY $RES/chunks" -c "load_verified doom_wad.bin 0xA0000000 $RES/chunks" \
  -c "reg pc $ENTRY" -c "resume" -c "shutdown" > $RES/openocd.log 2>&1
rc=$?
grep "load_verified:" $RES/openocd.log | tee -a $RES/run.txt
[ $rc = 0 ] && [ "$(grep -c 'load_verified: .* 0 bad' $RES/openocd.log)" = 2 ] || {
  echo "OpenOCD load failed (rc $rc), see $RES/openocd.log; press CPU_RESET and re-run with --no-program"; exit 1; }
step "DOOM is running (VGA monitor, USB keyboard in the HID port)"
if [ $TERM_ON = 1 ]; then
  echo "UART terminal: DOOM's messages below; keys typed here go to DOOM too. Quit: Ctrl-]"
  python3 doom_term.py "$UART" "$LOG"
else
  cat "$UART" | tee -a "$LOG"
fi
