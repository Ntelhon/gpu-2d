#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# SD-card image for DOOM on Cheshire + G2D (boot mode 1, DOOM side project): build/fpga/doom/doom_sd.img
#   partition 1: doom_loader (sw/apps/doom/doom_loader.c), type = Cheshire's zero-stage loader GUID, <= 48 KB
#   partition 2: doom.board.bin (DOOM's program, loaded to 0x8000_0000), type = Cheshire's firmware GUID
#   partition 3: doom_wad.bin (16-byte header + WAD, loaded to 0xA000_0000), type = DOOM WAD GUID (doom_loader.c)
# Inputs: build/fpga/doom/doom.board.elf and doom_wad.bin (make doom-sw), CHS_ROOT, RV_GCC_BIN.
# On the board machine:  sudo dd if=doom_sd.img of=/dev/sdX bs=1M conv=fsync && sudo sgdisk -e /dev/sdX
# then SW1..SW0 = 01 (boot mode 1) and reset.
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
CHS_ROOT=${CHS_ROOT:-$HOME/tools/src/cheshire}
RV_GCC_BIN=${RV_GCC_BIN:-$(dirname "$(command -v riscv64-unknown-elf-gcc)")}
OUT=${DOOM_OUT:-$ROOT/build/fpga/doom}
CC=$RV_GCC_BIN/riscv64-unknown-elf-gcc
OBJCOPY=$RV_GCC_BIN/riscv64-unknown-elf-objcopy
command -v sgdisk >/dev/null || { echo "mk_doom_sd: sgdisk (gdisk package) not found" >&2; exit 1; }
for f in "$OUT/doom.board.elf" "$OUT/doom_wad.bin"; do
  [ -f "$f" ] || { echo "mk_doom_sd: missing $f (make doom-sw)" >&2; exit 1; }
done

ZSL_TGUID=0269B26A-FD95-4CE4-98CF-941401412C62   # Cheshire sw/sw.mk CHS_SW_ZSL_TGUID
FW_TGUID=99EC86DA-3F5B-4B0D-8F4B-C4BACFA5F859    # Cheshire sw/sw.mk CHS_SW_FW_TGUID
WAD_TGUID=D00D0AD1-6B2D-4A5E-9F3A-5C0FFEE1D00D   # doom_loader.c DOOM_WAD_TYPE_GUID

# 1. the loader, built like Cheshire's ZSL (rom.ld: runs from the LLC scratchpad)
make -s -C "$CHS_ROOT" CHS_SW_GCC_BINROOT="$RV_GCC_BIN" "$CHS_ROOT/sw/lib/libcheshire.a"
# our object without LTO and with -mno-relax: GCC 13.2 (SiFive 3.0.6) rejects util.h gprw()'s gp clobber ("PIC
# register clobbered by 'gp'"; Cheshire's own zsl.c fails the same way with this compiler), so doom_loader.c swaps gp
# itself (see grread); Cheshire's LTO library is linked through the plugin as in build_sw.sh
FLAGS="-march=rv64gc_zifencei -mabi=lp64d -mstrict-align -O2 -Wall -Wextra -static -ffunction-sections -fdata-sections \
  -mcmodel=medany -mexplicit-relocs -fno-builtin -mno-relax"
"$CC" $FLAGS -I"$CHS_ROOT/sw/include" -I"$CHS_ROOT/sw/deps/printf" -c "$ROOT/sw/apps/doom/doom_loader.c" \
  -o "$OUT/doom_loader.o"
"$CC" $FLAGS -flto -fuse-linker-plugin -nostartfiles -Wl,--gc-sections -L"$CHS_ROOT/sw/link" -T"$CHS_ROOT/sw/link/rom.ld" \
  "$OUT/doom_loader.o" "$CHS_ROOT/sw/lib/libcheshire.a" -o "$OUT/doom_loader.rom.elf"
"$OBJCOPY" -O binary "$OUT/doom_loader.rom.elf" "$OUT/doom_loader.rom.bin"
"$OBJCOPY" -O binary "$OUT/doom.board.elf" "$OUT/doom.board.bin"

sectors() { echo $(( ($(stat -c %s "$1") + 511) / 512 )); }
L=$(sectors "$OUT/doom_loader.rom.bin"); P=$(sectors "$OUT/doom.board.bin"); W=$(sectors "$OUT/doom_wad.bin")
[ "$L" -le 96 ] || { echo "mk_doom_sd: loader is $L sectors, the boot ROM loads at most 96" >&2; exit 1; }
L0=64; P0=2048; W0=$(( (P0 + P + 2047) / 2048 * 2048 ))
END=$(( W0 + W + 2048 ))     # + room for the backup GPT
IMG=$OUT/doom_sd.img
rm -f "$IMG"
truncate -s $(( END * 512 )) "$IMG"
sgdisk --clear -g --set-alignment=1 \
  --new=1:$L0:$((L0 + 96 - 1)) --typecode=1:$ZSL_TGUID --change-name=1:doom_loader \
  --new=2:$P0:$((P0 + P - 1)) --typecode=2:$FW_TGUID --change-name=2:doom_program \
  --new=3:$W0:$((W0 + W - 1)) --typecode=3:$WAD_TGUID --change-name=3:doom_wad \
  "$IMG" >/dev/null
dd if="$OUT/doom_loader.rom.bin" of="$IMG" bs=512 seek=$L0 conv=notrunc status=none
dd if="$OUT/doom.board.bin" of="$IMG" bs=512 seek=$P0 conv=notrunc status=none
dd if="$OUT/doom_wad.bin" of="$IMG" bs=512 seek=$W0 conv=notrunc status=none
sgdisk -p "$IMG" | tail -4
echo "mk_doom_sd: $IMG ($(( $(stat -c %s "$IMG") >> 20 )) MB; loader $L, program $P, WAD $W sectors)"
