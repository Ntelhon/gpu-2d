#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# Fetch the DOOM engine and the free game data used by sw/apps/doom (side project DOOM, docs/PHASES.md).
# Nothing fetched here is committed: doomgeneric (GPL-2.0) goes to build/ext/doomgeneric, Freedoom (BSD-3) to
# build/ext/freedoom. A user's own doom1.wad / doom.wad is given with DOOM_WAD= at build / run time instead.
# Usage: scripts/fetch_doom.sh
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
EXT=$ROOT/build/ext

DG_COMMIT=e257a09527062bfb986b6e5e0780839b6cbd5b71   # ozkl/doomgeneric HEAD, checked 2026-10-27
FD_VER=0.13.0
FD_SHA=3f9b264f3e3ce503b4fb7f6bdcb1f419d93c7b546f4df3e874dd878db9688f59   # freedoom-0.13.0-CHECKSUM (signed)

mkdir -p "$EXT"
DG=$EXT/doomgeneric
if [ ! -d "$DG/.git" ]; then
  git clone -q https://github.com/ozkl/doomgeneric.git "$DG"
fi
git -C "$DG" checkout -q "$DG_COMMIT"
got=$(git -C "$DG" rev-parse HEAD)
[ "$got" = "$DG_COMMIT" ] || { echo "error: $DG is at $got, expected $DG_COMMIT"; exit 1; }

FD=$EXT/freedoom
ZIP=$FD/freedoom-$FD_VER.zip
mkdir -p "$FD"
if [ ! -f "$ZIP" ]; then
  curl -sSfL -o "$ZIP.part" "https://github.com/freedoom/freedoom/releases/download/v$FD_VER/freedoom-$FD_VER.zip"
  mv "$ZIP.part" "$ZIP"
fi
echo "$FD_SHA  $ZIP" | sha256sum -c --quiet - || { echo "error: $ZIP checksum mismatch"; exit 1; }
if [ ! -f "$FD/freedoom1.wad" ]; then
  python3 - "$ZIP" "$FD" <<'PY'
import sys, zipfile, os
z = zipfile.ZipFile(sys.argv[1])
for n in z.namelist():
    if n.endswith((".wad", "COPYING.txt", "CREDITS.txt")):
        with open(os.path.join(sys.argv[2], os.path.basename(n)), "wb") as f:
            f.write(z.read(n))
PY
fi
echo "doomgeneric $DG_COMMIT at $DG"
ls -la "$FD"/*.wad
