#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Build the G2D FreeRTOS test for Cheshire (v2.0): build/fpga/freertos/g2d_freertos.<variant>.elf and the expected
# CRCs from the host test with the same task / round counts (build/model/sw/test_freertos, FreeRTOS POSIX port).
# Inputs: CHS_ROOT, RV_GCC_BIN (default from PATH), FREERTOS_DIR (default ~/tools/src/FreeRTOS-Kernel-V11.3.3,
# scripts/fetch_freertos.sh), G2D_RTOS_TASKS (3), G2D_SEQ_ROUNDS (3), FPGA_SW_OUT (build/fpga/freertos).
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/../.." && pwd)
CHS_ROOT=${CHS_ROOT:-$HOME/tools/src/cheshire}
RV_GCC_BIN=${RV_GCC_BIN:-$(dirname "$(command -v riscv64-unknown-elf-gcc)")}
RTOS=${FREERTOS_DIR:-$HOME/tools/src/FreeRTOS-Kernel-V11.3.3}
OUT=${FPGA_SW_OUT:-$ROOT/build/fpga/freertos}
NT=${G2D_RTOS_TASKS:-3}
ROUNDS=${G2D_SEQ_ROUNDS:-3}
CC=$RV_GCC_BIN/riscv64-unknown-elf-gcc
[ -f "$RTOS/tasks.c" ] || "$ROOT/scripts/fetch_freertos.sh" "$RTOS"

mkdir -p "$OUT"
make -s -C "$CHS_ROOT" CHS_SW_GCC_BINROOT="$RV_GCC_BIN" "$CHS_ROOT/sw/lib/libcheshire.a"

# expected CRCs: the host test (model HAL) with the same tasks and rounds
HOST=${G2D_FREERTOS_HOST:-$(find "$ROOT/build/model" "$ROOT/build/rtos" -name test_freertos -type f 2>/dev/null | head -1)}
[ -n "$HOST" ] || { echo "build_freertos_sw: build/model has no test_freertos (make model with FreeRTOS fetched)"; exit 1; }
G2D_RTOS_TASKS=$NT G2D_SEQ_ROUNDS=$ROUNDS "$HOST" | grep '^FREERTOS_CRC' > "$OUT/expected.txt"

ARCH="-march=rv64gc_zifencei -mabi=lp64d -mcmodel=medany -mstrict-align"
CFLAGS="$ARCH -O2 -std=gnu11 -Wall -Wextra -ffp-contract=off -ffunction-sections -fdata-sections \
  -I$ROOT/sw/apps/freertos -I$ROOT/sw/include -I$ROOT/sw/include/generated -I$ROOT/sw/hal -I$ROOT/sw/hal/freertos \
  -I$ROOT/sw/lib/geom -I$ROOT/sw/driver/vg_lite -I$ROOT/sw/tests/freertos \
  -I$RTOS/include -I$RTOS/portable/GCC/RISC-V -I$RTOS/portable/GCC/RISC-V/chip_specific_extensions/RISCV_MTIME_CLINT_no_extensions \
  -I$CHS_ROOT/sw/include -I$CHS_ROOT/sw/deps/printf \
  -DG2D_BAREMETAL -DG2D_CSR_BASE=0x48000000u -DG2D_POOL_ADDR=0x81000000u -DG2D_POOL_BYTES=0x02000000u \
  -DG2D_HAL_IRQ=0 -DG2D_RTOS_TASKS=$NT -DG2D_SEQ_ROUNDS=$ROUNDS"
SRCS="$ROOT/sw/driver/vg_lite/drv_*.c $ROOT/sw/lib/geom/g2d_geom.c $ROOT/sw/hal/baremetal/g2d_hal_baremetal.c
  $ROOT/sw/apps/baremetal/syscalls.c $ROOT/sw/hal/freertos/g2d_freertos.c $ROOT/sw/tests/freertos/g2d_seq.c
  $ROOT/sw/apps/freertos/main_chs.c $RTOS/tasks.c $RTOS/list.c $RTOS/queue.c $RTOS/portable/MemMang/heap_4.c
  $RTOS/portable/GCC/RISC-V/port.c $RTOS/portable/GCC/RISC-V/portASM.S"

for v in board sim; do
  baud=115200; [ $v = sim ] && baud=1562500
  objs=()
  mkdir -p "$OUT/obj_$v"
  for s in $SRCS; do
    o="$OUT/obj_$v/$(basename "${s%.*}").o"
    # shellcheck disable=SC2086
    $CC $CFLAGS -DG2D_UART_BAUD=$baud -c "$s" -o "$o"
    objs+=("$o")
  done
  $CC $ARCH -static -nostartfiles -flto -fuse-linker-plugin -T"$CHS_ROOT/sw/link/dram.ld" -L"$CHS_ROOT/sw/link" \
    -Wl,--gc-sections "${objs[@]}" "$CHS_ROOT/sw/lib/libcheshire.a" -lm -lc -lgcc -o "$OUT/g2d_freertos.$v.elf"
  "$RV_GCC_BIN/riscv64-unknown-elf-size" "$OUT/g2d_freertos.$v.elf" | tail -1
done
echo "build_freertos_sw: $OUT/g2d_freertos.{board,sim}.elf, expected CRCs $OUT/expected.txt"
