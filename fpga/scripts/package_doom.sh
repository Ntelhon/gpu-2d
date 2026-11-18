#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Collect what the board machine needs to play DOOM (DOOM side project, step D6) into build/fpga/doom_pkg/ and
# build/fpga/doom_pkg.tar.gz. Inputs (built before): the DOOM bitstream (make fpga FPGA_TRIM=1 FPGA_DOOM=1) and the
# program + WAD image (make doom-sw). The board machine runs ./run_doom.sh in the unpacked directory.
# The WAD is Freedoom (BSD-3, its COPYING.txt is packed) unless DOOM_WAD pointed make doom-sw at another one; a
# package with a commercial WAD must not be shared.
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
PKG=$ROOT/build/fpga/doom_pkg
SW=$ROOT/build/fpga/doom
BIT=$(ls "$ROOT"/build/fpga/impl*_doom/g2d_chs*_doom.genesys2.bit 2>/dev/null | head -1)
[ -n "$BIT" ] || { echo "package_doom: no DOOM bitstream (make fpga FPGA_TRIM=1 FPGA_DOOM=1)" >&2; exit 1; }
for f in "$SW/doom.board.elf" "$SW/doom_wad.bin"; do
  [ -f "$f" ] || { echo "package_doom: missing $f (make doom-sw)" >&2; exit 1; }
done
rm -rf "$PKG"
mkdir -p "$PKG/openocd"
cp "$BIT" "$SW/doom.board.elf" "$SW/doom_wad.bin" "$PKG/"
cp "$ROOT/fpga/scripts/run_doom.sh" "$ROOT/fpga/scripts/doom_term.py" "$ROOT/fpga/genesys2/program.tcl" "$PKG/"
cp "$ROOT/fpga/genesys2/openocd_g2d.tcl" "$PKG/openocd/"
[ -f "$ROOT/build/ext/freedoom/COPYING.txt" ] && cp "$ROOT/build/ext/freedoom/COPYING.txt" "$PKG/FREEDOOM_COPYING.txt"
OBJCOPY=${OBJCOPY:-riscv64-unknown-elf-objcopy}
command -v "$OBJCOPY" >/dev/null || { echo "package_doom: $OBJCOPY not in PATH (source scripts/env.sh)" >&2; exit 1; }
"$OBJCOPY" -O binary "$PKG/doom.board.elf" "$PKG/doom.board.bin"
python3 - "$PKG/doom.board.elf" "$PKG/doom.board.bin" <<'PY'
import os, struct, sys
d = open(sys.argv[1], "rb").read()
entry, phoff = struct.unpack_from("<QQ", d, 24)
phentsize, phnum = struct.unpack_from("<HH", d, 54)
loads = [struct.unpack_from("<IIQQQQ", d, phoff + i * phentsize) for i in range(phnum)]
loads = [p for p in loads if p[0] == 1 and p[5]]
if len(loads) != 1 or loads[0][4] != entry or loads[0][5] != os.path.getsize(sys.argv[2]):
    sys.exit("package_doom: the ELF must have one LOAD segment starting at the entry point (loaded flat)")
PY
{
  echo "G2D DOOM package"
  echo "built:    $(date -Iseconds)"
  echo "git:      $(git -C "$ROOT" rev-parse HEAD)$(git -C "$ROOT" diff --quiet || echo ' (dirty)')"
  echo
  sha256sum "$PKG"/*.bit "$PKG"/*.elf "$PKG"/*.bin | sed "s#$PKG/##"
} > "$PKG/MANIFEST.txt"
tar -C "$ROOT/build/fpga" -czf "$ROOT/build/fpga/doom_pkg.tar.gz" doom_pkg
echo "package_doom: $ROOT/build/fpga/doom_pkg.tar.gz"
cat "$PKG/MANIFEST.txt"
