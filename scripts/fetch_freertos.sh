#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# Fetch the FreeRTOS kernel used by sw/hal/freertos and its tests (v2.0, user 2026-11-12; MIT licence, not stored in
# this repository).
# Usage: scripts/fetch_freertos.sh [dest]   (default ~/tools/src/FreeRTOS-Kernel-V11.3.3)
set -euo pipefail
VER=V11.3.3
COMMIT=89592801dd8b73329a7583719b7e3567b50910e5   # tag V11.3.3, checked 2026-11-12
DEST=${1:-$HOME/tools/src/FreeRTOS-Kernel-V11.3.3}
if [ ! -d "$DEST/.git" ]; then
  git clone -q --depth 1 --branch "$VER" https://github.com/FreeRTOS/FreeRTOS-Kernel.git "$DEST"
fi
got=$(git -C "$DEST" rev-parse HEAD)
[ "$got" = "$COMMIT" ] || { echo "error: $DEST is at $got, expected $COMMIT ($VER)"; exit 1; }
echo "FreeRTOS kernel $VER at $DEST"
