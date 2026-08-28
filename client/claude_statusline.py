#!/usr/bin/env python3
"""Run the configured Claude status line.

Existing Claude settings name this stable file. Usage now comes from Claude's
structured usage request, so this file only passes the input to the configured
status-line command.
"""

from __future__ import annotations

import subprocess
import sys


def delegate(argv: list[str], stdin_bytes: bytes) -> int:
    """Run the real status-line command with the same stdin, inheriting stdout
    and stderr so its output is what Claude Code renders. 0 when there is no
    delegate — a bare shim install still succeeds."""
    if not argv:
        return 0
    try:
        return subprocess.run(argv, input=stdin_bytes, check=False).returncode
    except Exception:
        return 0                                # never break the status line


def main() -> None:
    raw = sys.stdin.buffer.read()
    sys.exit(delegate(sys.argv[1:], raw))


if __name__ == "__main__":
    main()
