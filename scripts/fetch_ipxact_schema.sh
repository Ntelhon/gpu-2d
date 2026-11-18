#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# Fetch the IEEE 1685-2014 (IP-XACT) schema into build/ext/ipxact for scripts/tests/test_packaging.py::test_ipxact_schema.
# Source: the EDA Alliance mirror of the Accellera schemas (github.com/edaa-org/IPXACT-Schema, files of the Accellera
# Systems Initiative, own licence in the repository). The official host www.accellera.org redirects to plain http, which
# restricted networks cannot reach. Not stored in this repository.
set -e
ROOT=$(cd "$(dirname "$0")/.." && pwd)
D="$ROOT/build/ext/ipxact"
if [ ! -f "$D/ieee-1685-2014/index.xsd" ]; then
  rm -rf "$D"; mkdir -p "$D"
  git clone -q --depth 1 https://github.com/edaa-org/IPXACT-Schema.git "$D/repo"
  mv "$D/repo/ieee-1685-2014" "$D/ieee-1685-2014"
  cp "$D/repo/LICENSE.md" "$D/repo/NOTICE.md" "$D/"
  (cd "$D/repo" && git log -1 --format='%H' > "$D/COMMIT")
  rm -rf "$D/repo"
fi
xmllint --noout --schema "$D/ieee-1685-2014/index.xsd" "$ROOT/ipxact/g2d_top.xml"
