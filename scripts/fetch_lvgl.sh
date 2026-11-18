#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# Fetch the LVGL release used by sw/apps/lvgl (Phase 6 answer Q2 a: shared tools, not in the repo).
# Usage: scripts/fetch_lvgl.sh [dest]   (default ~/tools/src/lvgl-9.6.0)
set -euo pipefail
VER=v9.6.0
COMMIT=80ca777e37a2b176770726a02e07a6fb79ef0b39   # tag v9.6.0, checked 2026-10-16
DEST=${1:-$HOME/tools/src/lvgl-9.6.0}
if [ ! -d "$DEST/.git" ]; then
  git clone -q --depth 1 --branch "$VER" https://github.com/lvgl/lvgl.git "$DEST"
fi
got=$(git -C "$DEST" rev-parse HEAD)
[ "$got" = "$COMMIT" ] || { echo "error: $DEST is at $got, expected $COMMIT ($VER)"; exit 1; }
echo "LVGL $VER at $DEST"
