#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Check the UART log of the bare-metal FPGA test (sw/apps/baremetal/g2d_fpga_test.c) against the expected output
of the same scripts on the software backend (scripts/fpga_scenes.py expected.txt). Bit-exact: every return code
in order and every buffer CRC-32 must match (docs/PROJECT_RULES.md rule 7). Works on board logs and SoC simulation logs.

v1.2: "SWEEP <N>" ... "SWEEP_END <N>" sections (DRAM-delay sweep of the G2D_SWEEP board build) repeat the scene
set; every section is checked the same way. When expected.txt has "SWEEP_LIST a,b,..." every listed section must be
present. LAT_PROBE lines (latency probe) are reported, not judged. "LVGL <name> pages= bytes= crc=" in expected.txt
(scripts/fpga_lvgl.py): the replayed LVGL frame must print the same counts and CRC with err=00000000 in the main run
and in every sweep step. "VGA crc=" in expected.txt: the VGA test's VGA_ON line must show the same frame CRC.

v1.8 (Q-FPGA-10): SCENE_END lines may carry "wsig=<WR_SIG> coll=<DBG_STATUS>" (register map 0.3+, arch.md 9.1). Any
coll other than 00 fails the scene (a RAM read/write collision happened). With --wsig-ref <other log> (normally the
SoC simulation's log of the same ELF) the per-scene write signatures are compared and the first differing scene is
reported: it is the first scene in which the hardware wrote different bytes, even where the final buffer CRCs agree.

Usage: fpga_check.py <expected.txt> <uart.log> [--report <file.md>] [--wsig-ref <uart.log>]
Exit 0 when everything matches and the run ended with G2D_TEST_END fails=0 and IRQ_TEST ok=1.
"""
import argparse
import re
import sys

EXP_VGA = None   # frame CRC the VGA test must print (expected.txt "VGA crc="), set by parse_expected


def parse_expected(path):
    scenes, cur = {}, None
    sweep, lvgl = [], None
    global EXP_VGA
    for ln in open(path):
        ln = ln.strip()
        if ln.startswith("VGA "):
            EXP_VGA = ln.split("crc=")[1]
        elif ln.startswith("LVGL "):
            lvgl = ln
        elif ln.startswith("SWEEP_LIST "):
            sweep = [int(x) for x in ln.split()[1].split(",")]
        elif ln.startswith("SCENE "):
            _, k, name = ln.split()
            cur = scenes.setdefault(int(k), {"name": name, "rc": [], "crc": []})
        elif ln.startswith("rc "):
            cur["rc"].append(ln)
        elif ln.startswith("crc "):
            cur["crc"].append(ln)
    return scenes, sweep, lvgl


def parse_log(path):
    """Scenes per section: None = the main run, N = sweep step with DRAM delay N."""
    text = open(path, errors="replace").read().replace("\r", "")
    sections = {None: {}}
    lvgl = {}
    sec, cur = None, None
    info = {"begin": None, "end": None, "irq": None, "probes": [], "sweep_done": set()}
    for ln in text.split("\n"):
        ln = ln.strip()
        scenes = sections[sec]
        if ln.startswith("SWEEP_END"):
            info["sweep_done"].add(int(ln.split()[1]))
            sec, cur = None, None
        elif ln.startswith("SWEEP "):
            sec, cur = int(ln.split()[1]), None
            sections[sec] = {}
        elif ln.startswith("LVGL "):
            lvgl[sec] = ln
        elif ln.startswith("VGA_ON"):
            info["vga"] = ln
        elif ln.startswith("LAT_PROBE"):
            info["probes"].append(ln)
        elif ln.startswith("G2D_TEST_BEGIN"):
            info["begin"] = ln
        elif ln.startswith("SCENE_END"):
            m = re.match(r"SCENE_END (\d+) ret=(-?\d+) busy=(\d+) draws=(\d+)", ln)
            if m and cur is not None:
                cur.update(ret=int(m.group(2)), busy=int(m.group(3)), draws=int(m.group(4)))
                extra = dict(x.split("=", 1) for x in ln.split()[5:] if "=" in x)
                if "wsig" in extra:
                    cur.update(wsig=extra["wsig"], coll=int(extra.get("coll", "0"), 16))
            cur = None
        elif ln.startswith("SCENE "):
            f = ln.split()
            cur = scenes.setdefault(int(f[1]), {"name": f[2], "rc": [], "crc": []})
        elif ln.startswith("rc ") and cur is not None:
            cur["rc"].append(ln)
        elif ln.startswith("crc ") and cur is not None:
            cur["crc"].append(ln)
        elif ln.startswith("IRQ_TEST"):
            info["irq"] = ln
        elif ln.startswith("G2D_TEST_END"):
            info["end"] = ln
    info["lvgl"] = lvgl
    return sections, info


def lvgl_status(exp_line, got_line):
    """'' when the LVGL replay matches, else the reason."""
    if exp_line is None:
        return ""
    if got_line is None:
        return "LVGL frame missing"
    e = dict(x.split("=", 1) for x in exp_line.split()[2:])
    g = dict(x.split("=", 1) for x in got_line.split()[2:] if "=" in x)
    why = [k for k in ("pages", "bytes", "crc") if g.get(k) != e[k]]
    if got_line.split()[1] != exp_line.split()[1]:
        why.append("name")
    if g.get("err") != "00000000":
        why.append("err=%s" % g.get("err"))
    if "error" in g:
        why.append("error=%s" % g["error"])
    return "" if not why else "LVGL frame: %s (got `%s`)" % (", ".join(why), got_line)


def check_section(exp, got):
    rows, bad = [], 0
    for k in sorted(exp):
        e, g = exp[k], got.get(k)
        if g is None:
            rows.append((k, e["name"], "MISSING", "", "", ""))
            bad += 1
            continue
        why = []
        if g["rc"] != e["rc"]:
            first = next((i for i, (x, y) in enumerate(zip(g["rc"], e["rc"])) if x != y), min(len(g["rc"]), len(e["rc"])))
            why.append("rc #%d: got %r, expected %r" % (first, g["rc"][first] if first < len(g["rc"]) else None,
                                                       e["rc"][first] if first < len(e["rc"]) else None))
        if g["crc"] != e["crc"]:
            why.append("crc: got %s, expected %s" % (g["crc"], e["crc"]))
        if g.get("ret", None) != 0:
            why.append("runner ret=%s" % g.get("ret"))
        status = "PASS" if not why else "FAIL"
        bad += bool(why)
        rows.append((k, e["name"], status, g.get("busy", ""), g.get("draws", ""), "; ".join(why)))
    return rows, bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("expected")
    ap.add_argument("log")
    ap.add_argument("--report")
    ap.add_argument("--wsig-ref", help="log of the same ELF (SoC simulation) to compare write signatures with")
    a = ap.parse_args()
    exp, sweep_list, exp_lvgl = parse_expected(a.expected)
    sections, info = parse_log(a.log)
    rows, bad = check_section(exp, sections[None])
    lv_why = {None: lvgl_status(exp_lvgl, info["lvgl"].get(None))}
    bad += bool(lv_why[None])
    steps = sorted(set(sweep_list) | (set(sections) - {None}))
    sweep_rows, sweep_bad = {}, 0
    for n in steps:
        if n not in sections or n not in info["sweep_done"]:
            sweep_bad += 1
            sweep_rows[n] = None
            continue
        sweep_rows[n], b = check_section(exp, sections[n])
        lv_why[n] = lvgl_status(exp_lvgl, info["lvgl"].get(n))
        sweep_bad += b + bool(lv_why[n])
    vga_why = ""
    if EXP_VGA:
        v = info.get("vga")
        got = dict(x.split("=", 1) for x in v.split()[1:]).get("crc") if v else None
        vga_why = "" if got == EXP_VGA else "VGA_ON crc %s, expected %s" % (got, EXP_VGA)
        bad += bool(vga_why)
    # v1.8: RAM collisions and write signatures (main run)
    coll = {k: v["coll"] for k, v in sections[None].items() if v.get("coll")}
    bad += len(coll)
    wsig_why = ""
    if a.wsig_ref:
        ref_sections, _ = parse_log(a.wsig_ref)
        ref = ref_sections[None]
        for k in sorted(set(sections[None]) & set(ref)):
            g, r = sections[None][k].get("wsig"), ref[k].get("wsig")
            if g is None or r is None:
                wsig_why = "scene %d: no wsig in %s" % (k, "the log" if g is None else "the reference")
                break
            if g != r:
                wsig_why = "first scene with different writes: %d %s (wsig %s, reference %s)" % (
                    k, sections[None][k]["name"], g, r)
                break
        bad += bool(wsig_why)
    irq_ok = bool(info["irq"] and info["irq"].endswith("ok=1"))
    end_ok = bool(info["end"] and info["end"].endswith("fails=0"))
    ok = bad == 0 and sweep_bad == 0 and irq_ok and end_ok
    lines = ["# G2D FPGA test check", "", "- log: `%s`" % a.log, "- %s" % (info["begin"] or "no G2D_TEST_BEGIN"),
             "- %s" % (info["irq"] or "no IRQ_TEST line"), "- %s" % (info["end"] or "no G2D_TEST_END (run incomplete)"),
             "- scenes: %d, mismatches: %d" % (len(exp), bad)]
    if steps:
        lines.append("- DRAM-delay sweep steps: %s, mismatches or missing steps: %d" %
                     (",".join(map(str, steps)), sweep_bad))
    if coll:
        lines.append("- RAM read/write collisions (DBG_STATUS.RAM_COLL, arch.md 9.1): " +
                     ", ".join("scene %d: %02x" % kv for kv in sorted(coll.items())))
    if a.wsig_ref:
        lines.append("- write signatures vs `%s`: %s" % (a.wsig_ref, wsig_why or "all scenes equal"))
    lines += ["- result: **%s**" % ("PASS" if ok else "FAIL"), "",
              "| # | scene | result | busy cycles | draws | detail |", "|---|---|---|---|---|---|"]
    lines += ["| %s | %s | %s | %s | %s | %s |" % r for r in rows]
    if exp_lvgl:
        g = info["lvgl"].get(None)
        busy = dict(x.split("=", 1) for x in g.split()[2:] if "=" in x).get("busy", "") if g else ""
        lines.append("| lvgl | %s | %s | %s | | %s |" % (exp_lvgl.split()[1], "FAIL" if lv_why[None] else "PASS", busy,
                                                     lv_why[None]))
    if EXP_VGA:
        lines.append("| vga | frame buffer CRC at VGA_ON | %s | | | %s |" % ("FAIL" if vga_why else "PASS", vga_why))
    if info["probes"]:
        lines += ["", "## Latency probe (busy cycles of a NOP-only command buffer: one DRAM round trip, min/max)", "",
                  "| extra DRAM delay | busy cycles | repeats |", "|---|---|---|"]
        for p in info["probes"]:
            f = dict(x.split("=", 1) for x in p.split()[1:] if "=" in x)
            lines.append("| %s | %s | %s |" % (f.get("d", "?"), f.get("busy", f.get("error", "?")), f.get("n", "")))
    if steps:
        lines += ["", "## DRAM-delay sweep (busy cycles per scene; extra delay N on AR and AW, SoC cycles)", "",
                  "| # | scene | " + " | ".join("N=%d" % n for n in steps) + " |", "|---|---|" + "---|" * len(steps)]
        for k in sorted(exp):
            cells = []
            for n in steps:
                r = sweep_rows[n]
                if r is None:
                    cells.append("missing")
                else:
                    row = next(x for x in r if x[0] == k)
                    cells.append(str(row[3]) if row[2] == "PASS" else "%s %s" % (row[2], row[5]))
            lines.append("| %d | %s | %s |" % (k, exp[k]["name"], " | ".join(cells)))
        if exp_lvgl:
            cells = []
            for n in steps:
                g = info["lvgl"].get(n)
                if lv_why.get(n, "missing"):
                    cells.append(lv_why.get(n) or "missing")
                else:
                    cells.append(dict(x.split("=", 1) for x in g.split()[2:] if "=" in x).get("busy", ""))
            lines.append("| lvgl | %s | %s |" % (exp_lvgl.split()[1], " | ".join(cells)))
    report = "\n".join(lines) + "\n"
    print(report)
    if a.report:
        open(a.report, "w").write(report)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
