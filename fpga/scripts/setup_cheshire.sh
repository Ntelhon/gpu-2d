#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Set up the Cheshire SoC checkout used by the G2D FPGA build (Phase 7).
#
#   CHS_ROOT   where Cheshire lives (default: $HOME/tools/src/cheshire); not part of this repository
#   CHS_PY     Python with Cheshire's generator packages (default: $HOME/tools/cheshire-venv/bin/python)
#   BENDER     bender binary (default: bender from PATH)
#
# Steps (idempotent): clone, check out the pinned commit, apply fpga/cheshire/cheshire-g2d.patch,
# fetch the bender dependencies, generate Cheshire's hardware sources (make chs-hw-all).
set -euo pipefail

CHS_REPO=https://github.com/pulp-platform/cheshire.git
CHS_REV=6c6dd5e834347f72c7be31cd6c41021ec293c5b9   # main, 2026-10-14 (verified 2026-10-17)

HERE=$(cd "$(dirname "$0")/.." && pwd)
CHS_ROOT=${CHS_ROOT:-$HOME/tools/src/cheshire}
CHS_PY=${CHS_PY:-$HOME/tools/cheshire-venv/bin/python}
BENDER=${BENDER:-bender}
PATCH=$HERE/cheshire/cheshire-g2d.patch

if [ ! -d "$CHS_ROOT/.git" ]; then
  git clone "$CHS_REPO" "$CHS_ROOT"
fi
cd "$CHS_ROOT"
if [ "$(git rev-parse HEAD)" != "$CHS_REV" ]; then
  git fetch -q origin
  git checkout -q "$CHS_REV"
fi

if git apply --reverse --check "$PATCH" 2>/dev/null; then
  echo "setup_cheshire: patch already applied"
else
  # an older version of the patch may be applied (v1.8 added the LLC cached-region parameter): restore the files the
  # patch touches to the pinned commit, then apply the current one
  git checkout -q -- $(git apply --numstat "$PATCH" | awk '{print $3}')
  git apply "$PATCH"
  rm -f .bender/git/checkouts/opentitan_peripherals-*/.generated   # regenerate the PLIC
  echo "setup_cheshire: patch applied"
fi

"$BENDER" checkout

# Cheshire's generators need its Python packages (pyproject.toml); put that Python first in PATH.
PATH=$(dirname "$CHS_PY"):$PATH make chs-hw-all

grep -q "NumSrc = 59" .bender/git/checkouts/opentitan_peripherals-*/src/rv_plic/rtl/rv_plic_reg_pkg.sv || {
  echo "setup_cheshire: PLIC was not regenerated with 59 sources" >&2; exit 1; }
echo "setup_cheshire: OK ($CHS_ROOT at $CHS_REV)"
