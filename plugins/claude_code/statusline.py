#!/usr/bin/env python3
"""Forwarding shim: pre-refactor statusline capture path.

The plugins/ -> harness/impl move renamed this capture half out from under any
already-running session whose statusLine config still names it. Forwards to the
moved statusline so the capture keeps working; the delegate command and argv
pass through unchanged.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from harness.impl.claude_code.hooks.statusline import main  # noqa: E402


if __name__ == "__main__":
    main()
