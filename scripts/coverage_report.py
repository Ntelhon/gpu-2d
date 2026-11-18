#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Merge the functional coverage files of the L3 benches (build/coverage/*.json, dv/common/g2d_coverage.py)
into build/coverage/report.md. --require-full: exit 1 if any Phase 3 group has a hole."""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dv" / "common"))
sys.path.insert(0, str(ROOT / "scripts"))

import g2d_coverage as cov  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dir", default=str(ROOT / "build" / "coverage"))
    ap.add_argument("--require-full", action="store_true")
    args = ap.parse_args(argv)
    files = sorted(Path(args.dir).glob("*.json"))
    if not files:
        print(f"no coverage files in {args.dir}")
        return 1
    text, full = cov.report(cov.merge(files))
    out = Path(args.dir) / "report.md"
    out.write_text(text)
    print(text.split("\n## Holes")[0])
    print(f"{len(files)} files merged -> {out}")
    return 0 if full or not args.require_full else 1


if __name__ == "__main__":
    sys.exit(main())
