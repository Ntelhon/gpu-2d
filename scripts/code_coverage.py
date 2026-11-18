#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""RTL code coverage (verification-plan.md §4: line >= 95 % on rtl/, Phase 5).

    scripts/code_coverage.py                 run the make-test suite with coverage, merge, report
    scripts/code_coverage.py --report-only   merge and report the existing build/coverage/code/dat/*.dat

Runs scripts/regress.py --suite test with G2D_CODE_COV=1 (coverage builds in build/dv/<top>_<tag>_cov,
Verilator --coverage-line --coverage-toggle, dv/ and generated wrappers excluded by dv/sva/coverage.vlt),
merges the per-run .dat files with verilator_coverage and writes build/coverage/code/:
  merged.dat, line.info / toggle.info (lcov), annotate/ (annotated sources), report.md.
Lines kept out of the line figure, each listed in the report with its reason:
  excluded       simulation-only code (`ifndef SYNTHESIS blocks) and, in generated files, the CSR read-mux
                 default and function bodies (g2d_state_pkg functions are only called in localparams:
                 evaluated at elaboration, never at run time) (assumption A2 of the Phase 5 plan)
  not measurable case items whose statement is a `return` (Verilator 5.052 attaches no count to them: the
                 function entry is counted, its return-only arms read 0; the functional coverage groups
                 cover them, e.g. cg_blend for the 16 blend modes)
  exception      FSM `default:` recovery arms (state encodings no transition produces)
The raw figure (no exclusions) is reported too. Exit status 1 if the line coverage of rtl/ is below --min
(default 95).
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "build" / "coverage" / "code"


RETURN_ARM = re.compile(r"^[^/]*:\s*return\b")
FSM_DEFAULT = re.compile(r"^default:\s*\w+_q\s*<=\s*[A-Z][A-Z0-9_]*;")


def classify(path: Path) -> dict[int, str]:
    """line -> category for lines kept out of the figure (see the module docstring)."""
    cat = {}
    text = path.read_text().splitlines()
    for ln in excluded_lines(path, text):
        cat[ln] = "excluded"
    for i, ln in enumerate(text, 1):
        s = ln.strip()
        if i in cat:
            continue
        if RETURN_ARM.match(s):
            cat[i] = "not measurable"
        elif FSM_DEFAULT.match(s):
            cat[i] = "exception"
    return cat


def excluded_lines(path: Path, text: list[str]) -> set[int]:
    """Lines of `ifndef SYNTHESIS blocks; in generated files the read-mux default and function bodies."""
    ex, depth, func = set(), 0, False
    gen = "generated" in path.parts
    for i, ln in enumerate(text, 1):
        s = ln.strip()
        if gen and s.startswith("function"):
            func = True
        if func:
            ex.add(i)
            if s.startswith("endfunction"):
                func = False
        if depth:
            ex.add(i)
            if s.startswith("`ifdef") or s.startswith("`ifndef"):
                depth += 1
            elif s.startswith("`endif"):
                depth -= 1
        elif s.startswith("`ifndef SYNTHESIS"):
            ex.add(i)
            depth = 1
        if gen and s.startswith("default:"):
            ex.add(i)
    return ex


def parse_info(path: Path) -> dict[str, dict[int, int]]:
    """lcov DA records: file -> line -> hit count (max over the points of a line)."""
    cov: dict[str, dict[int, int]] = defaultdict(dict)
    cur = None
    for ln in path.read_text().splitlines():
        if ln.startswith("SF:"):
            cur = ln[3:]
        elif ln.startswith("DA:") and cur:
            line, cnt = ln[3:].split(",")[:2]
            cov[cur][int(line)] = max(cov[cur].get(int(line), 0), int(cnt))
    return cov


def vcov(*args):
    subprocess.run(["verilator_coverage", *args], cwd=ROOT, check=True, capture_output=True, text=True)


def summary(dat: Path) -> dict[str, str]:
    out = subprocess.run(["verilator_coverage", "--report", "summary", str(dat)], cwd=ROOT, check=True,
                         capture_output=True, text=True).stdout
    return dict(re.findall(r"^\s*(\w+)\s*:\s*(.+?)\s*$", out, re.M))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--report-only", action="store_true")
    ap.add_argument("--seeds", default="1")
    ap.add_argument("--min", type=float, default=95.0, help="minimum line coverage of rtl/ in percent")
    args = ap.parse_args(argv)
    dat_dir = OUT / "dat"
    if not args.report_only:
        if dat_dir.exists():
            shutil.rmtree(dat_dir)
        env = {**os.environ, "G2D_CODE_COV": "1"}
        rc = subprocess.run([sys.executable, str(ROOT / "scripts" / "regress.py"), "--suite", "test", "--seeds",
                             args.seeds, "--out", str(ROOT / "build" / "regress" / "codecov"),
                             "--coverage-report", ""], cwd=ROOT, env=env).returncode
        if rc:
            print("code coverage: some runs failed (coverage still reported)")
    dats = sorted(str(p) for p in dat_dir.glob("*.dat"))
    if not dats:
        raise SystemExit("no coverage data in build/coverage/code/dat")
    merged = OUT / "merged.dat"
    vcov("--write", str(merged), *dats)
    vcov("--filter-type", "line", "--write-info", str(OUT / "line.info"), str(merged))
    vcov("--filter-type", "toggle", "--write-info", str(OUT / "toggle.info"), str(merged))
    if (OUT / "annotate").exists():
        shutil.rmtree(OUT / "annotate")
    vcov("--annotate", str(OUT / "annotate"), "--annotate-min", "1", str(merged))
    tog = summary(merged).get("toggle", "?")

    lines = parse_info(OUT / "line.info")
    rows, tot_hit, tot_all, raw_hit, raw_all, holes = [], 0, 0, 0, 0, []
    kept: dict[str, list[str]] = defaultdict(list)
    for f in sorted(lines):
        p = Path(f)
        if ROOT / "rtl" not in p.parents:
            continue
        rel = p.relative_to(ROOT)
        cat = classify(p)
        raw_hit += sum(1 for c in lines[f].values() if c)
        raw_all += len(lines[f])
        # excluded lines never count; the other categories only keep uncovered lines out
        pts = {ln: c for ln, c in lines[f].items() if not (ln in cat and (cat[ln] == "excluded" or c == 0))}
        for ln in sorted(lines[f]):
            if ln in cat and lines[f][ln] == 0:
                kept[cat[ln]].append(f"{rel}:{ln}: `{p.read_text().splitlines()[ln - 1].strip()[:90]}`")
        hit = sum(1 for c in pts.values() if c)
        tot_hit, tot_all = tot_hit + hit, tot_all + len(pts)
        rows.append((str(rel), hit, len(pts)))
        holes += [f"{rel}:{ln}" for ln, c in sorted(pts.items()) if c == 0]
    pct = 100.0 * tot_hit / tot_all if tot_all else 0.0
    md = ["# RTL code coverage", "",
          f"Generated by scripts/code_coverage.py from {len(dats)} runs (make-test suite, seeds {args.seeds}).", "",
          f"- Line coverage, rtl/ (lines kept out listed below): **{pct:.1f} %** ({tot_hit}/{tot_all}), goal {args.min:.0f} %",
          f"- Raw line coverage, rtl/ (nothing kept out): {100.0 * raw_hit / raw_all:.1f} % ({raw_hit}/{raw_all})",
          f"- Toggle coverage, all instrumented signals: {tog}", "",
          "| File | Lines hit | Lines | Coverage |", "|---|---|---|---|"]
    md += [f"| {f} | {h} | {n} | {100.0 * h / n if n else 100:.1f} % |" for f, h, n in rows]
    md += ["", f"## Uncovered lines ({len(holes)})", "", "Annotated sources: build/coverage/code/annotate/.", ""]
    md += [f"- {h}" for h in holes]
    for c in ("excluded", "not measurable", "exception"):
        md += ["", f"## Uncovered lines kept out: {c} ({len(kept[c])})", ""] + [f"- {h}" for h in kept[c]]
    (OUT / "report.md").write_text("\n".join(md) + "\n")
    print("\n".join(md[:6]))
    print(f"report: {(OUT / 'report.md').relative_to(ROOT)}")
    return 0 if pct >= args.min else 1


if __name__ == "__main__":
    sys.exit(main())
