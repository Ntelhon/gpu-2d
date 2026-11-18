#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Vocabulary check of the G2D SDC template (v1.9, D-2): every command is a standard SDC 2.x command or plain Tcl
control flow, and every option is one the SDC standard defines for that command. No timing is computed: the real
check is an STA run (docs/OPEN_QUESTIONS.md Q-INT-2). Exit 1 on an unknown command or option."""

import re
import sys
from pathlib import Path

TCL = {"if", "set", "proc", "expr", "info", "return"}
SDC = {
    "create_clock": {"-name", "-period", "-waveform", "-add", "-comment"},
    "set_clock_uncertainty": {"-from", "-to", "-rise", "-fall", "-setup", "-hold"},
    "set_input_delay": {"-clock", "-clock_fall", "-level_sensitive", "-rise", "-fall", "-max", "-min", "-add_delay",
                        "-network_latency_included", "-source_latency_included"},
    "set_output_delay": {"-clock", "-clock_fall", "-level_sensitive", "-rise", "-fall", "-max", "-min", "-add_delay",
                         "-network_latency_included", "-source_latency_included"},
    "set_case_analysis": set(),
    "set_max_delay": {"-rise", "-fall", "-from", "-to", "-through", "-rise_from", "-rise_to", "-fall_from",
                      "-fall_to", "-ignore_clock_latency", "-comment"},
    "set_false_path": {"-setup", "-hold", "-rise", "-fall", "-from", "-to", "-through", "-comment"},
    "get_ports": {"-quiet", "-regexp", "-nocase", "-filter"},
    "get_clocks": {"-quiet", "-regexp", "-nocase"},
    "all_clocks": set(),
}


def main(path):
    text = Path(path).read_text()
    code = "\n".join(ln.split("#", 1)[0] if not ln.lstrip().startswith("#") else "" for ln in text.splitlines())
    bad = []
    # commands at the start of a statement or after "[" / "{"
    for m in re.finditer(r"(?:^|[\[{;]|\n)\s*([a-z_]+)((?:[^\]\n;{}]|\{[^}]*\})*)", code):
        cmd, rest = m.group(1), m.group(2)
        if cmd in TCL:
            continue
        if cmd not in SDC and not cmd.startswith("g2d_"):
            bad.append(f"unknown command {cmd}")
            continue
        for opt in re.findall(r"(?<![\w$])(-[a-z_]+)", rest):
            if cmd in SDC and opt not in SDC[cmd]:
                bad.append(f"{cmd}: unknown option {opt}")
    if code.count("{") != code.count("}") or code.count("[") != code.count("]"):
        bad.append("unbalanced braces / brackets")
    for b in bad:
        print(f"check_sdc: {path}: {b}")
    print(f"check_sdc: {path}: {'FAIL' if bad else 'OK'}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "syn/constraints/g2d_top.sdc"))
