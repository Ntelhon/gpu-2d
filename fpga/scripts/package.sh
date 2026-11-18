#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Collect everything the board machine needs into build/fpga/pkg/ and build/fpga/g2d_board_pkg.tar.gz (Phase 7).
# Inputs (built before): build/fpga/impl/g2d_chs.genesys2.bit (make fpga), build/fpga/sw/ (make fpga-sw), and when
# present build/fpga/impl_async/g2d_chs_async.genesys2.bit (make fpga FPGA_ASYNC=1, v1.1; run_board.sh --async).
# The board machine runs ./run_board.sh in the unpacked directory (docs/FPGA.md).
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/../.." && pwd)
CHS_ROOT=${CHS_ROOT:-$HOME/tools/src/cheshire}
PKG=$ROOT/build/fpga/pkg
BIT=$ROOT/build/fpga/impl/g2d_chs.genesys2.bit
BIT_ASYNC=$ROOT/build/fpga/impl_async/g2d_chs_async.genesys2.bit
if [ "${G2D_FPGA_TRIM:-0}" = 1 ]; then   # v1.7: trimmed Cheshire (make fpga FPGA_TRIM=1 [FPGA_ASYNC=1])
  BIT=$ROOT/build/fpga/impl_trim/g2d_chs_trim.genesys2.bit
  BIT_ASYNC=$ROOT/build/fpga/impl_async_trim/g2d_chs_async_trim.genesys2.bit
fi
SW=$ROOT/build/fpga/sw

for f in "$BIT" "$SW/g2d_fpga_test.board.elf" "$SW/expected.txt"; do
  [ -f "$f" ] || { echo "package: missing $f (make fpga / make fpga-sw)" >&2; exit 1; }
done
rm -rf "$PKG"
mkdir -p "$PKG/openocd"
cp "$BIT" "$SW/g2d_fpga_test.board.elf" "$SW/expected.txt" "$PKG/"
if [ -f "$BIT_ASYNC" ]; then cp "$BIT_ASYNC" "$PKG/"; else echo "package: no async bitstream (make fpga FPGA_ASYNC=1)"; fi
cp "$ROOT/fpga/scripts/run_board.sh" "$ROOT/fpga/genesys2/program.tcl" "$ROOT/scripts/fpga_check.py" "$PKG/"
# v1.2: what the VGA monitor should show (the LVGL frame the replay leaves in memory), as a PPM image
if [ -f "$SW/lvgl_fb.raw" ]; then
  python3 "$ROOT/scripts/vga_check.py" "$SW/lvgl_fb.raw" "$SW/lvgl_fb.raw" --ppm "$PKG/vga_expected.ppm" >/dev/null
fi
# OpenOCD config with the CRC-checked loader, and the program as a flat image for it (the ELF's one LOAD segment)
cp "$ROOT/fpga/genesys2/openocd_g2d.tcl" "$PKG/openocd/"
OBJCOPY=${OBJCOPY:-riscv64-unknown-elf-objcopy}
command -v "$OBJCOPY" >/dev/null || { echo "package: $OBJCOPY not in PATH (source scripts/env.sh)" >&2; exit 1; }
"$OBJCOPY" -O binary "$PKG/g2d_fpga_test.board.elf" "$PKG/g2d_fpga_test.board.bin"
python3 - "$PKG/g2d_fpga_test.board.elf" "$PKG/g2d_fpga_test.board.bin" <<'PY'
import os, struct, sys
d = open(sys.argv[1], "rb").read()
entry, phoff = struct.unpack_from("<QQ", d, 24)
phentsize, phnum = struct.unpack_from("<HH", d, 54)
loads = [struct.unpack_from("<IIQQQQ", d, phoff + i * phentsize) for i in range(phnum)]
loads = [p for p in loads if p[0] == 1 and p[5]]
if len(loads) != 1 or loads[0][4] != entry or loads[0][5] != os.path.getsize(sys.argv[2]):
    sys.exit("package: the board ELF must have one LOAD segment starting at the entry point (run_board.sh loads it flat)")
PY
{
  echo "G2D board package"
  echo "built:    $(date -Iseconds)"
  echo "git:      $(git -C "$ROOT" rev-parse HEAD)$(git -C "$ROOT" diff --quiet || echo ' (dirty)')"
  echo "cheshire: $(git -C "$CHS_ROOT" rev-parse HEAD)"
  echo
  sha256sum "$PKG"/*.bit "$PKG"/*.elf "$PKG"/*.bin "$PKG"/expected.txt | sed "s#$PKG/##"
} > "$PKG/MANIFEST.txt"
tar -C "$ROOT/build/fpga" -czf "$ROOT/build/fpga/g2d_board_pkg.tar.gz" pkg
echo "package: $ROOT/build/fpga/g2d_board_pkg.tar.gz"
cat "$PKG/MANIFEST.txt"
