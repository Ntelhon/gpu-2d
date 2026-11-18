#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Mutation campaign over the RTL (Phase 5 plan item 8: at least 3 mutants per block).

    scripts/mutate.py                      generate, run and report (16 workers)
    scripts/mutate.py --list               print the mutants only
    scripts/mutate.py --files rtl/fill/g2d_fill.sv --per-file 6

Mutants are generated deterministically (--seed) from operator patterns on run-time RTL code (comments,
declarations, `ifndef SYNTHESIS blocks and generated files are skipped): relational and equality swaps,
&/| and &&/|| swaps, dropped ~ / !, 1'b0 <-> 1'b1 in assignments, +/- 1 -> 0. --per-file lines per file,
each line mutated once.

Each worker owns a copy of the tree (tracked files + generated register files) under build/mutate/w<k>,
applies one mutant at a time and runs the kill tests in order until one fails: the block bench(es) of
the file (BLOCK_TESTS), then the top-level benches of both configurations (TOP_TESTS). A mutant is
  killed     a test failed (mismatch, checker, assertion, timeout through G2D_WAIT_LIMIT)
  survived   every kill test passed: triaged by hand in the report (equivalent mutant or test gap)
  invalid    it does not build (warnings are not fatal for mutants: G2D_NO_FATAL)
Workers run one process at a time with make -j1, so --workers is also the core count (max 16).
Report: build/mutate/report.md and results.json.
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import os
import random
import re
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "build" / "mutate"
MAX_WORKERS = 16

# (name, pattern, replacement); patterns apply to the code part of one line
OPERATORS = [
    ("eq->ne", r"(?<![=!<>])==(?!=)", "!="),
    ("ne->eq", r"!=(?!=)", "=="),
    ("and->or", r"(?<=\s)&(?=\s)", "|"),
    ("or->and", r"(?<=\s)\|(?=\s)", "&"),
    ("land->lor", r"&&", "||"),
    ("lor->land", r"\|\|", "&&"),
    ("drop~", r"~(?=[\w(])", ""),
    ("drop!", r"!(?=[\w(])", ""),
    ("lt->le", r"(?<=\s)<(?=\s)", "<="),
    ("gt->ge", r"(?<=\s)>(?=\s)", ">="),
    ("ge->gt", r"(?<=\s)>=(?=\s)", ">"),
    ("1->0", r"<= 1'b1;", "<= 1'b0;"),
    ("0->1", r"<= 1'b0;", "<= 1'b1;"),
    ("inc->0", r"([+-]) (\d+'[dh])?1\b(?!')", r"\1 \g<2>0"),
]

BLOCK_TESTS = {
    "g2d_fifo": ["dv/tests/test_common.py"], "g2d_fifo_ram": ["dv/tests/test_common.py"],
    "g2d_ram_1r1w": ["dv/tests/test_common.py"], "g2d_ram_sp": ["dv/tests/test_common.py"],
    "g2d_rr_arb": ["dv/tests/test_common.py"], "g2d_spill_reg": ["dv/tests/test_common.py"],
    "g2d_sdiv": ["dv/tests/test_sdiv.py"],
    "g2d_axil2reg": ["dv/tests/test_csr_top.py"], "g2d_csr_top": ["dv/tests/test_csr_top.py"],
    "g2d_ctrl": ["dv/tests/test_csr_top.py"], "g2d_perf": ["dv/tests/test_csr_top.py"],
    "g2d_axi_mst": ["dv/tests/test_axi_mst.py"], "g2d_axi_rd": ["dv/tests/test_axi_mst.py"],
    "g2d_axi_wr": ["dv/tests/test_axi_mst.py"],
    "g2d_draw_check": ["dv/tests/test_draw_check.py"], "g2d_blend": ["dv/tests/test_blend.py"],
    "g2d_xform": ["dv/tests/test_xform.py"], "g2d_tex_addr": ["dv/tests/test_sampler.py"],
    "g2d_texel": ["dv/tests/test_sampler.py", "dv/tests/test_blit.py::test_blit[dw64]"],   # v1.6b: YUV, colour
    "g2d_src_cache": ["dv/tests/test_src_cache.py"],
    "g2d_tex_seq": ["dv/tests/test_blit.py::test_blit[dw64]"], "g2d_tex_split": ["dv/tests/test_blit.py::test_blit[dw64]"],
    "g2d_tex_merge": ["dv/tests/test_blit.py::test_blit[dw64]"],
    "g2d_edge_load": ["dv/tests/test_edge_load.py"], "g2d_raster": ["dv/tests/test_raster.py"],
    "g2d_wind_acc": ["dv/tests/test_raster.py"], "g2d_grad": ["dv/tests/test_grad.py"],
    "g2d_ramp": ["dv/tests/test_grad.py"], "g2d_pkg": ["dv/tests/test_pkg.py"],
    "g2d_isqrt": ["dv/tests/test_grad.py"],
    "g2d_mask_rd": ["dv/tests/test_blit.py::test_blit[dw64]"],
}
TOP_TESTS = ["dv/tests/test_top.py::test_top[dw64]", "dv/tests/test_sys.py::test_sys[dw64]",
             "dv/tests/test_blit.py::test_blit[dw64]", "dv/tests/test_path.py::test_path[dw64-b]",
             "dv/tests/test_path.py::test_path[dw64-a]", "dv/tests/test_sys.py::test_sys[dw128]",
             "dv/tests/test_top.py::test_top[dw128]", "dv/tests/test_blit.py::test_blit[dw128]",
             "dv/tests/test_path.py::test_path[dw128]"]


@dataclass
class Mutant:
    id: int
    file: str
    line: int
    op: str
    orig: str
    new: str
    status: str = ""
    killed_by: str = ""
    error: str = ""
    seconds: float = 0.0


KEYWORDS = {"if", "else", "begin", "end", "for", "int", "unsigned", "signed", "assign", "return", "case", "unique",
             "default", "logic", "automatic", "endcase"}


def param_only(code: str) -> bool:
    """True when the line refers to no signal (parameters, constants, keywords): elaboration-time code."""
    idents = re.findall(r"(?<!')\b[A-Za-z_]\w*\b", re.sub(r"\d+'[sbdh][0-9a-fA-F_xz]+", "", code))
    return not [i for i in idents if i not in KEYWORDS and not i.isupper() and not i.startswith("g_")]


def code_lines(path: Path):
    """(line number, text) of run-time code lines eligible for mutation."""
    depth = 0
    for i, ln in enumerate(path.read_text().splitlines(), 1):
        s = ln.strip()
        if depth:
            if s.startswith(("`ifdef", "`ifndef")):
                depth += 1
            elif s.startswith("`endif"):
                depth -= 1
            continue
        if s.startswith("`ifndef SYNTHESIS"):
            depth = 1
            continue
        code = ln.split("//")[0]
        c = code.strip()
        if not c or c.startswith(("module", "parameter", "localparam", "input", "output", "import", "typedef",
                                  "`", "function", "logic", "genvar", "int ", "bit ")):
            continue
        if param_only(c) or re.search(r"\$(fatal|error|display|warning)", c):
            continue
        yield i, code


def generate(files: list[Path], per_file: int, seed: int) -> list[Mutant]:
    muts = []
    for f in files:
        rng = random.Random(f"{seed}:{f.name}")
        cands = []
        for i, code in code_lines(f):
            for name, pat, rep in OPERATORS:
                for m in re.finditer(pat, code):
                    new = code[:m.start()] + m.expand(rep) + code[m.end():]
                    if new != code:
                        cands.append((i, name, m.start(), new))
        rng.shuffle(cands)
        used = set()
        picked = []                          # distinct lines, at most per_file / 3 of one operator
        for c in cands:
            if c[0] in used:
                continue
            if sum(1 for p in picked if p[1] == c[1]) >= max(1, per_file // 3):
                continue
            used.add(c[0])
            picked.append(c)
            if len(picked) == per_file:
                break
        for c in cands:                      # fill up if the operator spread left gaps
            if len(picked) == per_file:
                break
            if c[0] not in used:
                used.add(c[0])
                picked.append(c)
        lines = f.read_text().splitlines()
        for i, name, _, new in sorted(picked):
            orig = lines[i - 1]
            comment = orig[len(orig.split("//")[0]):]
            muts.append(Mutant(len(muts), str(f.relative_to(ROOT)), i, name, orig.strip(), (new + comment).strip()))
    return muts


def make_copy(dst: Path):
    if dst.exists():
        shutil.rmtree(dst)
    files = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()
    for rel in files:
        src = ROOT / rel
        if src.is_file():
            (dst / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst / rel)
    subprocess.run([sys.executable, str(dst / "scripts" / "gen_regs.py")], cwd=dst, check=True, capture_output=True)


def run_node(copy: Path, node: str, timeout: int) -> tuple[int, str]:
    env = {**os.environ, "SEED": "1", "G2D_FULL": "0", "G2D_WAIT_LIMIT": "300000", "G2D_FAIL_FAST": "1", "G2D_BUILD_JOBS": "1",
           "G2D_NO_FATAL": "1", "G2D_MODEL_PATH": os.environ.get("G2D_MODEL_PATH", str(ROOT / "build" / "model"))}
    p = subprocess.Popen([sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", node], cwd=copy,
                         env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, start_new_session=True)
    try:
        out, _ = p.communicate(timeout=timeout)
        return p.returncode, out
    except subprocess.TimeoutExpired:
        os.killpg(p.pid, signal.SIGKILL)
        out, _ = p.communicate()
        return -1, out + f"\nTIMEOUT after {timeout} s"


def first_error(text: str) -> str:
    for pat in [r"(FAIL_FAST: .*)", r"(%Error[-\w]*: .*)", r"(p_\w+ \(.*?\): .*)", r"(AssertionError: .*)", r"(TIMEOUT after .*)",
                r"(\w+Error: .*)"]:
        m = re.search(pat, text)
        if m:
            return m.group(1).strip()[:160]
    return ""


def run_mutant(m: Mutant, copy: Path, timeout: int) -> Mutant:
    t0 = time.time()
    path = copy / m.file
    orig = path.read_text()
    lines = orig.splitlines(keepends=True)
    assert lines[m.line - 1].strip() == m.orig, (m.file, m.line)
    indent = lines[m.line - 1][:len(lines[m.line - 1]) - len(lines[m.line - 1].lstrip())]
    lines[m.line - 1] = indent + m.new + "\n"
    path.write_text("".join(lines))
    try:
        mod = Path(m.file).stem
        nodes = BLOCK_TESTS.get(mod, []) + TOP_TESTS
        m.status = "survived"
        for node in nodes:
            rc, out = run_node(copy, node, timeout)
            if rc == 0:
                continue
            build_failed = "Error" in out and ("Exiting due to" in out or "make: ***" in out) \
                and "cocotb.regression" not in out
            m.status = "invalid" if build_failed else "killed"
            m.killed_by = node
            m.error = first_error(out)
            break
    finally:
        path.write_text(orig)
    m.seconds = round(time.time() - t0, 1)
    return m


def report(muts: list[Mutant], out: Path = OUT):
    by_file: dict[str, list[Mutant]] = {}
    for m in muts:
        by_file.setdefault(m.file, []).append(m)
    valid = [m for m in muts if m.status in ("killed", "survived")]
    killed = [m for m in valid if m.status == "killed"]
    md = ["# Mutation campaign", "",
          f"{len(muts)} mutants, {len(valid)} valid, {len(killed)} killed, "
          f"{len(valid) - len(killed)} survived, {len(muts) - len(valid)} invalid (do not build). "
          f"Mutation score {100.0 * len(killed) / max(1, len(valid)):.1f} %.", "",
          "| File | Valid | Killed | Survived | Invalid |", "|---|---|---|---|---|"]
    for f, ms in sorted(by_file.items()):
        v = [m for m in ms if m.status in ("killed", "survived")]
        md.append(f"| {f} | {len(v)} | {sum(m.status == 'killed' for m in v)} | "
                  f"{sum(m.status == 'survived' for m in v)} | {len(ms) - len(v)} |")
    md += ["", "## Survivors (triage below each)", ""]
    for m in muts:
        if m.status == "survived":
            md += [f"- #{m.id} {m.file}:{m.line} ({m.op}): `{m.orig}` -> `{m.new}`"]
    md += ["", "## All mutants", "", "| # | Location | Operator | Status | Killed by | First error |", "|---|---|---|---|---|---|"]
    for m in muts:
        md.append(f"| {m.id} | {m.file}:{m.line} | {m.op} | {m.status} | {m.killed_by.split('/')[-1]} | "
                  f"{m.error.replace('|', '/')[:100]} |")
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.md").write_text("\n".join(md) + "\n")
    (out / "results.json").write_text(json.dumps([asdict(m) for m in muts], indent=1))
    print("\n".join(md[:3]))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--files", nargs="*", help="RTL files (default: every non-generated rtl/ file)")
    ap.add_argument("--per-file", type=int, default=4)
    ap.add_argument("--seed", type=int, default=5)
    ap.add_argument("--workers", type=int, default=MAX_WORKERS)
    ap.add_argument("--timeout", type=int, default=1200, help="per test node, seconds")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--ids", type=int, nargs="*", help="run only these mutant ids (from a full listing)")
    ap.add_argument("--out", default=str(OUT), help="report directory")
    args = ap.parse_args(argv)
    files = [ROOT / f for f in args.files] if args.files else sorted(
        p for p in (ROOT / "rtl").rglob("*.sv") if "generated" not in p.parts)
    muts = generate(files, args.per_file, args.seed)
    if args.ids is not None:
        muts = [m for m in muts if m.id in set(args.ids)]
    out = Path(args.out)
    if args.list:
        for m in muts:
            print(f"#{m.id} {m.file}:{m.line} {m.op}: {m.orig}  ->  {m.new}")
        return 0
    OUT.mkdir(parents=True, exist_ok=True)
    workers = min(args.workers, MAX_WORKERS)
    copies = [OUT / f"w{k}" for k in range(workers)]
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(make_copy, copies))
    free = list(copies)
    done = []
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {}
        it = iter(muts)
        def submit():
            m = next(it, None)
            if m is not None:
                c = free.pop()
                futs[ex.submit(run_mutant, m, c, args.timeout)] = c
        for _ in range(workers):
            submit()
        while futs:
            fin, _ = cf.wait(futs, return_when=cf.FIRST_COMPLETED)
            for f in fin:
                free.append(futs.pop(f))
                m = f.result()
                done.append(m)
                print(f"[{len(done)}/{len(muts)}] #{m.id} {m.status:8s} {m.file}:{m.line} {m.op} "
                      f"({m.seconds:.0f} s) {m.killed_by.split('/')[-1]}", flush=True)
                submit()
    report(sorted(done, key=lambda m: m.id), out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
