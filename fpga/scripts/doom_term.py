#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Minimal UART terminal for run_doom.sh (DOOM side project): prints and logs what the board sends, sends the keys
typed here (raw mode, one byte per key; arrows as ESC [ A..D). Ctrl-] quits. Standard library only.

    doom_term.py <tty> <log file>     (the tty is already set to 115200 8N1 raw by run_doom.sh)
"""
import os
import select
import sys
import termios
import tty


def main() -> int:
    dev, logname = sys.argv[1], sys.argv[2]
    fd = os.open(dev, os.O_RDWR | os.O_NOCTTY)
    log = open(logname, "ab")
    stdin = sys.stdin.fileno()
    old = termios.tcgetattr(stdin) if os.isatty(stdin) else None
    try:
        if old:
            tty.setraw(stdin)
        while True:
            r, _, _ = select.select([fd, stdin], [], [])
            if fd in r:
                data = os.read(fd, 4096)
                log.write(data)
                log.flush()
                os.write(sys.stdout.fileno(), data)
            if stdin in r:
                data = os.read(stdin, 64)
                if not data or b"\x1d" in data:   # Ctrl-]
                    break
                os.write(fd, data)
    finally:
        if old:
            termios.tcsetattr(stdin, termios.TCSADRAIN, old)
        log.close()
        os.close(fd)
    print("\r\ndoom_term: closed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
