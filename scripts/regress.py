#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Parallel regression runner (verification-plan.md §6).

    scripts/regress.py --suite benches --seeds 101-110 --full
    scripts/regress.py --suite sys --seeds 1-300
    scripts/regress.py --suite model --seeds 101-110
    scripts/regress.py --suite test --seeds 1            (make test: everything once, short mode)

Suites: `benches` (every cocotb bench of REGRESS_DV in the Makefile, one pytest node per configuration),
`sys` (dv/tests/test_sys.py), `model` (the seeded golden-model and driver-scene suites), `test`
(scripts/tests, model/tests, sw/tests and every node of dv/tests), or explicit pytest nodes with --nodes. Each (node, seed) is one
run; the longest runs (times of the previous run, build/regress_times.json) start first. Nodes that
compare the outputs of other runs of the same seed (names containing `_same`) run after them.
Every simulator is built once (G2D_BUILD_ONLY, make -j1, longest builds first) and its runs start as soon
as it is built (G2D_SKIP_BUILD, per-run result files), all on one pool of --jobs slots: the core budget
holds during builds too, and parallel runs never share a build or a result file.

At most 8 parallel jobs (project limit, user 2026-11-05; was 32). Output in --out (default build/regress):
summary.md (pass/fail table, failures grouped by first error message, rerun commands), junit.xml,
one log per run, and the merged functional coverage report (scripts/coverage_report.py).
"""

from __future__ import annotations

import argparse
import fcntl
import json
import concurrent.futures as cf
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parents[1]
MAX_JOBS = 8
MODEL_SUITES = ["model/tests/test_stages.py", "model/tests/test_executor.py", "sw/tests/test_scenes.py",
                "sw/tests/test_stroke.py", "model/tests/test_ideal.py"]


def parse_seeds(text: str) -> list[int]:
    seeds = []
    for part in text.split(","):
        if "-" in part:
            a, b = part.split("-")
            seeds += list(range(int(a), int(b) + 1))
        elif part:
            seeds.append(int(part))
    return seeds


TIMES = ROOT / "build" / "regress_times.json"
_PROCS: set = set()     # running children; each is a process-group leader (pytest and its simulator)


def run_group(cmd, env, timeout=None):
    """Run cmd in its own process group; on timeout (or when this script is stopped) the whole group is
    killed, so no simulator outlives its run."""
    p = subprocess.Popen(cmd, cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                         start_new_session=True)
    _PROCS.add(p)
    try:
        out, _ = p.communicate(timeout=timeout)
        return p.returncode, out
    except subprocess.TimeoutExpired:
        os.killpg(p.pid, signal.SIGKILL)
        out, _ = p.communicate()
        return -1, (out or "") + f"\nTIMEOUT after {timeout} s"
    finally:
        _PROCS.discard(p)


def _stop(signum, frame):
    for p in list(_PROCS):
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    raise SystemExit(128 + signum)


def regress_dv() -> list[str]:
    """REGRESS_DV of the Makefile (single source for the bench list)."""
    out = subprocess.run(["make", "-s", "-p", "-n", "regress"], cwd=ROOT, capture_output=True, text=True).stdout
    m = re.search(r"^REGRESS_DV := (.*)$", out, re.M)
    if not m:
        raise SystemExit("REGRESS_DV not found in the Makefile")
    return m.group(1).split()


def collect(files: list[str]) -> list[str]:
    out = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q", *files], cwd=ROOT,
                         capture_output=True, text=True)
    nodes = [ln.strip() for ln in out.stdout.splitlines() if "::" in ln]
    if out.returncode != 0 or "ERROR collecting" in out.stdout:
        raise SystemExit("collection errors (no test may be skipped silently):\n" + out.stdout[-4000:] + out.stderr)
    if not nodes:
        raise SystemExit("no tests collected:\n" + out.stdout + out.stderr)
    return nodes


def env_for(seed: int, full: bool, extra: dict) -> dict:
    env = dict(os.environ)
    env["SEED"] = str(seed)
    env["G2D_FULL"] = "1" if full else "0"
    env.setdefault("G2D_MODEL_PATH", str(ROOT / "build" / "model"))
    env.update(extra)
    return env


def run_one(node: str, seed: int, full: bool, logdir: Path, extra: dict, timeout: int) -> dict:
    tag = re.sub(r"[^A-Za-z0-9_.-]+", "_", node)
    log = logdir / f"{tag}__s{seed}.log"
    t0 = time.time()
    rc, out = run_group([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", node],
                        env_for(seed, full, {**extra, "G2D_SKIP_BUILD": "1"}), timeout)
    log.write_text(out)
    return {"node": node, "seed": seed, "ok": rc == 0, "time": time.time() - t0, "log": log,
            "error": first_error(out) if rc != 0 else ""}


def first_error(text: str) -> str:
    """The first line that names the failure (assertion, checker message, simulator error, timeout)."""
    pats = [r"Assertion failed in .*?: (p_\w+.*)", r"(p_\w+ \(.*?\): .*)", r"(AssertionError: .*)",
            r"(%Error: .*)", r"(TIMEOUT after .*)", r"((?<!Runtime)\b\w+Error: .*)", r"(RuntimeError: .*)",
            r"(E\s+.*Error.*)"]
    for pat in pats:
        m = re.search(pat, text)
        if m:
            return m.group(1).strip()[:200]
    return "failed (no error line found)"


def normalize(msg: str) -> str:
    """Error message with numbers and addresses removed, for grouping."""
    return re.sub(r"0x[0-9a-fA-F]+|\d+", "#", msg)


def build_dirs(nodes: list[str], full: bool, extra: dict, jobs: int, tmp: Path) -> dict[str, str | None]:
    """Build directory of every node (dry run of the runner); None for nodes without a simulation."""
    def probe(i, node):
        f = tmp / f"bd_{i}.txt"
        f.unlink(missing_ok=True)
        run_group([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", node],
                  env_for(1, full, {**extra, "G2D_BUILD_DIR_FILE": str(f)}))
        dirs = f.read_text().split() if f.exists() else []
        return node, (dirs[0] if dirs else None)

    with cf.ThreadPoolExecutor(max_workers=jobs) as ex:
        return dict(ex.map(lambda a: probe(*a), enumerate(nodes)))


def report(r: dict, results: list):
    results.append(r)
    status = "ok  " if r["ok"] else "FAIL"
    print(f"[{len(results)}] {status} {r['node']} seed {r['seed']} ({r['time']:.0f} s)"
          + ("" if r["ok"] else f": {r['error']}"), flush=True)


def pipeline(bdirs: dict, seeds: list[int], args, extra: dict, jobs: int, out: Path, times: dict,
             results: list) -> bool:
    """Builds and runs on one pool of `jobs` slots: every simulator is built once (make -j1, longest
    builds first), and the runs of its nodes start as soon as it is built (longest runs first among the
    runs ready). Nodes without a simulation run at once. Returns False if a build failed."""
    by_dir: dict[str, list[str]] = {}
    for n, d in bdirs.items():
        by_dir.setdefault(d, []).append(n)
    ok = True

    def build(d):
        t0 = time.time()
        rc, txt = run_group([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", by_dir[d][0]],
                            env_for(1, args.full, {**extra, "G2D_BUILD_ONLY": "1", "G2D_BUILD_JOBS": "1"}))
        times["build:" + d] = round(time.time() - t0, 1)
        return d, rc, txt

    def runs_of(nodes):
        work = [(n, s) for s in seeds for n in nodes]
        work.sort(key=lambda w: -times.get(w[0], 1e9))
        return work

    with cf.ThreadPoolExecutor(max_workers=jobs) as ex:
        pending = set()
        for n, sd in runs_of(by_dir.get(None, [])):
            pending.add(ex.submit(run_one, n, sd, args.full, out / "logs", extra, args.timeout))
        for d in sorted((d for d in by_dir if d), key=lambda d: -times.get("build:" + d, 1e9)):
            pending.add(ex.submit(build, d))
        nbuilt = 0
        while pending:
            done, pending = cf.wait(pending, return_when=cf.FIRST_COMPLETED)
            for f in done:
                r = f.result()
                if isinstance(r, tuple):
                    d, rc, txt = r
                    nbuilt += 1
                    if rc:
                        ok = False
                        diag = [ln for ln in txt.splitlines() if ln.startswith(("%Error", "%Warning"))]
                        print(f"BUILD FAILED {by_dir[d][0]}\n" + ("\n".join(diag[:20]) if diag else txt[-3000:]))
                        continue
                    print(f"built {Path(d).name} ({times['build:' + d]:.0f} s, {nbuilt} simulators)", flush=True)
                    for n, sd in runs_of(by_dir[d]):
                        pending.add(ex.submit(run_one, n, sd, args.full, out / "logs", extra, args.timeout))
                else:
                    report(r, results)
    return ok


def write_reports(results: list[dict], out: Path, full: bool, wall: float):
    fails = [r for r in results if not r["ok"]]
    groups: dict[str, list[dict]] = {}
    for r in fails:
        groups.setdefault(normalize(r["error"]), []).append(r)
    lines = ["# Regression summary", "",
             f"{len(results)} runs, {len(results) - len(fails)} passed, {len(fails)} failed, wall time {wall / 60:.1f} min", "",
             "| Node | Seeds | Passed | Failed | Time (s, mean) |", "|---|---|---|---|---|"]
    by_node: dict[str, list[dict]] = {}
    for r in results:
        by_node.setdefault(r["node"], []).append(r)
    for node, rs in sorted(by_node.items()):
        nf = sum(1 for r in rs if not r["ok"])
        lines.append(f"| {node} | {len(rs)} | {len(rs) - nf} | {nf} | {sum(r['time'] for r in rs) / len(rs):.0f} |")
    if fails:
        lines += ["", "## Failures grouped by first error", ""]
        for key, rs in sorted(groups.items(), key=lambda kv: -len(kv[1])):
            lines.append(f"### {len(rs)} x {rs[0]['error']}")
            for r in rs[:10]:
                lines.append(f"- `SEED={r['seed']} G2D_FULL={int(full)} python3 -m pytest -q '{r['node']}'`"
                             f" (log {r['log'].relative_to(ROOT)})")
            lines.append("")
    (out / "summary.md").write_text("\n".join(lines) + "\n")
    cases = []
    for r in results:
        name = escape(f"{r['node']}[seed={r['seed']}]")
        body = "" if r["ok"] else f'<failure message="{escape(r["error"])}"/>'
        cases.append(f'  <testcase classname="g2d.regress" name="{name}" time="{r["time"]:.1f}">{body}</testcase>')
    (out / "junit.xml").write_text(f'<?xml version="1.0"?>\n<testsuite name="g2d-regress" tests="{len(results)}" '
                                   f'failures="{len(fails)}">\n' + "\n".join(cases) + "\n</testsuite>\n")
    return fails


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--suite", choices=["benches", "sys", "model", "test"], default="benches")
    ap.add_argument("--nodes", nargs="*", help="explicit pytest nodes instead of a suite")
    ap.add_argument("--seeds", default="101-110")
    ap.add_argument("--full", action="store_true", help="full-length mode (G2D_FULL=1)")
    ap.add_argument("--jobs", type=int, default=MAX_JOBS)
    ap.add_argument("--timeout", type=int, default=7200, help="per-run timeout in seconds")
    ap.add_argument("--out", default=str(ROOT / "build" / "regress"))
    ap.add_argument("--keep-coverage", action="store_true", help="do not clear build/coverage first")
    ap.add_argument("--coverage-report", choices=["", "show", "full"], default="show",
                    help="merged coverage report afterwards: show, full (fail unless 100%%), '' none")
    args = ap.parse_args(argv)
    jobs = min(args.jobs, MAX_JOBS)
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    # one regression at a time: runs share the build directories (a rebuild under a running simulation
    # gives undefined results)
    lock_path = ROOT / "build" / "regress.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock = open(lock_path, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit("another scripts/regress.py is running (build/regress.lock)")
    seeds = parse_seeds(args.seeds)
    out = Path(args.out).resolve()
    (out / "logs").mkdir(parents=True, exist_ok=True)

    if args.nodes:
        nodes = args.nodes
    elif args.suite == "model":
        nodes = collect(MODEL_SUITES)
    elif args.suite == "sys":
        nodes = collect(["dv/tests/test_sys.py"])
    elif args.suite == "test":
        # sw/tests one node per file (the scene, stroke, LVGL and live-RTL suites run in parallel)
        nodes = ["scripts/tests", "model/tests"] + sorted(str(f.relative_to(ROOT)) for f in (ROOT / "sw" / "tests").glob("test_*.py")) + \
            collect(["dv/tests"])
    else:
        nodes = collect(regress_dv())
    extra = {}
    t0 = time.time()
    post_nodes = [n for n in nodes if "_same" in n]           # compare the outputs of a seed's runs
    sim_nodes = [n for n in nodes if n not in post_nodes]
    bdirs = build_dirs(sim_nodes, args.full, extra, jobs, out) if args.suite != "model" or args.nodes else \
        {n: None for n in sim_nodes}
    if not args.keep_coverage:
        for f in (ROOT / "build" / "coverage").glob("*.json"):
            f.unlink()
    results = []
    try:
        times = json.loads(TIMES.read_text())
    except (OSError, ValueError):
        times = {}
    if not pipeline(bdirs, seeds, args, extra, jobs, out, times, results):
        return 1

    def run_batch(work, label):
        print(f"{label}: {len(work)} runs on {jobs} jobs", flush=True)
        with cf.ThreadPoolExecutor(max_workers=jobs) as ex:
            futs = {ex.submit(run_one, n, s, args.full, out / "logs", extra, args.timeout): (n, s) for n, s in work}
            for f in cf.as_completed(futs):
                report(f.result(), results)
    if post_nodes:
        run_batch([(n, sd) for sd in seeds for n in post_nodes], "post-checks")
    for r in results:
        if r["ok"]:
            times[r["node"]] = round(r["time"], 1)
    TIMES.parent.mkdir(parents=True, exist_ok=True)
    TIMES.write_text(json.dumps(times, indent=1, sort_keys=True))
    fails = write_reports(results, out, args.full, time.time() - t0)
    print((out / "summary.md").read_text().split("\n## Failures")[0])
    if args.coverage_report and any((ROOT / "build" / "coverage").glob("*.json")):
        cov = [sys.executable, str(ROOT / "scripts" / "coverage_report.py")]
        if args.coverage_report == "full":
            cov.append("--require-full")
        if subprocess.run(cov, cwd=ROOT).returncode:
            return 1
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
