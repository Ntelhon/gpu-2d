#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Build bare-metal DOOM for Cheshire + G2D (DOOM side project, step D4): build/fpga/doom/doom.<variant>.elf and the
# WAD image doom_wad.bin (16-byte header "G2DWAD\0\0" + 64-bit size, then the WAD) that run_doom.sh / the SoC
# simulation load at 0xA000_0000.
#   variant board: UART 115200 (Genesys2); sim: UART 1562500 (SoC simulation), stops after DOOM_SIM_FRAMES frames
# Inputs: CHS_ROOT, RV_GCC_BIN (default from PATH), DOOM_WAD (default build/ext/freedoom/freedoom1.wad),
#   DOOM_HAVE_PS2 (1 default: the DOOM bitstream with chs_ps2; 0: standard bitstream, UART keys only),
#   DOOM_SIM_FRAMES (default 3), DOOM_OUT (default build/fpga/doom).
# Needs scripts/fetch_doom.sh (doomgeneric in build/ext, GPL-2.0, not committed).
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
CHS_ROOT=${CHS_ROOT:-$HOME/tools/src/cheshire}
RV_GCC_BIN=${RV_GCC_BIN:-$(dirname "$(command -v riscv64-unknown-elf-gcc)")}
OUT=${DOOM_OUT:-$ROOT/build/fpga/doom}
DG=$ROOT/build/ext/doomgeneric/doomgeneric
WAD=${DOOM_WAD:-$ROOT/build/ext/freedoom/freedoom1.wad}
CC=$RV_GCC_BIN/riscv64-unknown-elf-gcc
[ -f "$DG/doomgeneric.h" ] || { echo "build_doom_sw: run scripts/fetch_doom.sh first" >&2; exit 1; }
[ -f "$WAD" ] || { echo "build_doom_sw: WAD $WAD not found" >&2; exit 1; }
mkdir -p "$OUT"

make -s -C "$CHS_ROOT" CHS_SW_GCC_BINROOT="$RV_GCC_BIN" "$CHS_ROOT/sw/lib/libcheshire.a"
python3 "$ROOT/scripts/gen_vglite_stubs.py" "$ROOT/sw/include/vg_lite.h" "$OUT/vg_lite_stubs.c" \
  "$ROOT"/sw/driver/vg_lite/drv_*.c

ARCH="-march=rv64gc_zifencei -mabi=lp64d -mcmodel=medany -mstrict-align"
COMMON="$ARCH -O2 -std=gnu11 -ffp-contract=off -ffunction-sections -fdata-sections"
G2D_CFLAGS="$COMMON -Wall -Wextra -I$ROOT/sw/include -I$ROOT/sw/include/generated -I$ROOT/sw/hal -I$ROOT/sw/lib/geom \
  -I$ROOT/sw/driver/vg_lite -I$CHS_ROOT/sw/include \
  -DG2D_BAREMETAL -DG2D_CSR_BASE=0x48000000u -DG2D_POOL_ADDR=0x81000000u -DG2D_POOL_BYTES=0x02000000u"
DOOM_DEFS="-DCMAP256 -DDOOMGENERIC_RESX=320 -DDOOMGENERIC_RESY=200"
ENGINE="dummy am_map doomdef doomstat dstrings d_event d_items d_iwad d_loop d_main d_mode d_net f_finale f_wipe
  g_game hu_lib hu_stuff info i_cdmus i_endoom i_joystick i_scale i_sound i_system i_timer memio m_argv m_bbox
  m_cheat m_config m_controls m_fixed m_menu m_misc m_random p_ceilng p_doors p_enemy p_floor p_inter p_lights p_map
  p_maputl p_mobj p_plats p_pspr p_saveg p_setup p_sight p_spec p_switch p_telept p_tick p_user r_bsp r_data r_draw
  r_main r_plane r_segs r_sky r_things sha1 sounds statdump st_lib st_stuff s_sound tables v_video wi_stuff
  w_checksum w_file w_main w_wad z_zone w_file_stdc i_input i_video doomgeneric"

for v in board sim; do
  baud=115200; frames=0
  if [ $v = sim ]; then baud=1562500; frames=${DOOM_SIM_FRAMES:-3}; fi
  O=$OUT/obj_$v
  mkdir -p "$O"
  objs=()
  for e in $ENGINE; do   # the engine: third-party code, its own warnings off
    # shellcheck disable=SC2086
    $CC $COMMON $DOOM_DEFS -w -I"$DG" -c "$DG/$e.c" -o "$O/dg_$e.o"
    objs+=("$O/dg_$e.o")
  done
  for s in "$ROOT"/sw/driver/vg_lite/drv_*.c "$ROOT/sw/lib/geom/g2d_geom.c" "$ROOT/sw/hal/baremetal/g2d_hal_baremetal.c" \
           "$OUT/vg_lite_stubs.c"; do
    # shellcheck disable=SC2086
    $CC $G2D_CFLAGS -c "$s" -o "$O/$(basename "${s%.*}").o"
    objs+=("$O/$(basename "${s%.*}").o")
  done
  for s in doom_chs doom_g2d doom_keys doom_syscalls; do
    # shellcheck disable=SC2086
    $CC $G2D_CFLAGS $DOOM_DEFS -I"$DG" -I"$ROOT/sw/apps/doom" -DDOOM_UART_BAUD=${baud}u -DDOOM_MAX_FRAMES=${frames}u \
      -DDOOM_HAVE_PS2=${DOOM_HAVE_PS2:-1} -DDOOM_LLC_CACHE=${DOOM_LLC_CACHE:-1} -DDOOM_TRACE=${DOOM_TRACE:-0} -c "$ROOT/sw/apps/doom/$s.c" -o "$O/$s.o"
    objs+=("$O/$s.o")
  done
  $CC $ARCH -static -nostartfiles -flto -fuse-linker-plugin -T"$ROOT/sw/apps/doom/doom_dram.ld" -L"$CHS_ROOT/sw/link" \
    -Wl,--gc-sections "${objs[@]}" "$CHS_ROOT/sw/lib/libcheshire.a" -lm -lc -lgcc -o "$OUT/doom.$v.elf"
  "$RV_GCC_BIN/riscv64-unknown-elf-size" "$OUT/doom.$v.elf" | tail -1
done

python3 - "$WAD" "$OUT/doom_wad.bin" <<'PY'
import struct, sys
d = open(sys.argv[1], "rb").read()
open(sys.argv[2], "wb").write(b"G2DWAD\0\0" + struct.pack("<Q", len(d)) + d)
print(f"doom_wad.bin: {len(d)} bytes of {sys.argv[1]}")
PY
