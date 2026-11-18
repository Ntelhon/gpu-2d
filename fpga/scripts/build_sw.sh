#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Build the bare-metal G2D test for Cheshire (Phase 7): build/fpga/sw/g2d_fpga_test.<variant>.elf
#   variant board: UART 115200 (Genesys2), variant sim: UART 1562500 = core_freq/32 (SoC simulation)
# Inputs: CHS_ROOT (Cheshire set up by setup_cheshire.sh), RV_GCC_BIN (dir of riscv64-unknown-elf-gcc; default
# from PATH), FPGA_SCENES_ARGS (passed to scripts/fpga_scenes.py; default 2 scenes per kind, seed 1, DRAM-delay
# sweep 0,8,16,32,64 (v1.2; the sweep needs Cheshire's USE_RAM_DELAY cfg registers, present on the Genesys2 build and
# in the SoC sim)),
# FPGA_LVGL_ARGS (passed to scripts/fpga_lvgl.py, v1.2: the LVGL frame; default widgets, RGB565, 640x480, 2 frames,
# needs make lvgl-chs; "--none" leaves it out), PYTHON (Python with the project packages, default python3),
# FPGA_SW_OUT (output dir, default build/fpga/sw).
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/../.." && pwd)
CHS_ROOT=${CHS_ROOT:-$HOME/tools/src/cheshire}
RV_GCC_BIN=${RV_GCC_BIN:-$(dirname "$(command -v riscv64-unknown-elf-gcc)")}
PYTHON=${PYTHON:-python3}
OUT=${FPGA_SW_OUT:-$ROOT/build/fpga/sw}
CC=$RV_GCC_BIN/riscv64-unknown-elf-gcc

mkdir -p "$OUT"
# Cheshire's runtime (crt0, UART / CLINT drivers) with its own flags (LTO objects)
make -s -C "$CHS_ROOT" CHS_SW_GCC_BINROOT="$RV_GCC_BIN" "$CHS_ROOT/sw/lib/libcheshire.a"

# Scenes and expected output from the software backend
$PYTHON "$ROOT/scripts/fpga_scenes.py" "$OUT" ${FPGA_SCENES_ARGS:---per-kind 2 --seed 1 --sweep 0,8,16,32,64}
$PYTHON "$ROOT/scripts/fpga_lvgl.py" "$OUT" ${FPGA_LVGL_ARGS:-}
$PYTHON "$ROOT/scripts/gen_vglite_stubs.py" "$ROOT/sw/include/vg_lite.h" "$OUT/vg_lite_stubs.c" \
  "$ROOT"/sw/driver/vg_lite/drv_*.c

ARCH="-march=rv64gc_zifencei -mabi=lp64d -mcmodel=medany -mstrict-align"
CFLAGS="$ARCH -O2 -std=gnu11 -Wall -Wextra -ffp-contract=off -ffunction-sections -fdata-sections \
  -I$ROOT/sw/include -I$ROOT/sw/include/generated -I$ROOT/sw/hal -I$ROOT/sw/lib/geom -I$ROOT/sw/driver/vg_lite \
  -I$CHS_ROOT/sw/include -I$CHS_ROOT/sw/deps/printf \
  -DG2D_BAREMETAL -DG2D_CSR_BASE=0x48000000u -DG2D_POOL_ADDR=0x81000000u -DG2D_POOL_BYTES=0x02000000u \
  -DG2D_LLC_CACHE=${G2D_LLC_CACHE:-0} -DG2D_FPGA_DBG=${G2D_FPGA_DBG:-1} -DG2D_HAL_IRQ=${G2D_HAL_IRQ:-0}"
SRCS="$ROOT/sw/driver/vg_lite/drv_*.c $ROOT/sw/lib/geom/g2d_geom.c $ROOT/sw/hal/baremetal/g2d_hal_baremetal.c \
  $ROOT/sw/tests/vgl_scene.c $ROOT/sw/apps/baremetal/g2d_fpga_test.c $ROOT/sw/apps/baremetal/syscalls.c $OUT/g2d_scenes.c $OUT/vg_lite_stubs.c $OUT/g2d_lvgl.S"

for v in board sim; do
  baud=115200; [ $v = sim ] && baud=1562500   # sim: divisor 2 at 50 MHz (divisor 1 does not work in the APB UART)
  # latency probe: board 8 repeats, sim 2; VGA hold: board 5 s, sim 40 ms
  # (two frames, enough for the testbench capture); all for simulation time
  vflags="-DG2D_UART_BAUD=$baud"; [ $v = sim ] && vflags="$vflags -DG2D_PROBE_REPEATS=2 -DG2D_VGA_HOLD_MS=40"
  objs=()
  mkdir -p "$OUT/obj_$v"
  # Our objects without LTO (GCC 13.2 LTO hits an internal error on them); Cheshire's LTO library is linked
  # through the plugin as in Cheshire's own build.
  for s in $SRCS; do
    o="$OUT/obj_$v/$(basename "${s%.*}").o"
    # shellcheck disable=SC2086
    $CC $CFLAGS $vflags -c "$s" -o "$o"
    objs+=("$o")
  done
  $CC $ARCH -static -nostartfiles -flto -fuse-linker-plugin -T"$CHS_ROOT/sw/link/dram.ld" -L"$CHS_ROOT/sw/link" \
    -Wl,--gc-sections "${objs[@]}" "$CHS_ROOT/sw/lib/libcheshire.a" -lm -lc -lgcc -o "$OUT/g2d_fpga_test.$v.elf"
  "$RV_GCC_BIN/riscv64-unknown-elf-size" "$OUT/g2d_fpga_test.$v.elf" | tail -1
done
echo "build_sw: $OUT/g2d_fpga_test.{board,sim}.elf, expected output $OUT/expected.txt"
