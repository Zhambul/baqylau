#!/usr/bin/env python3
"""Forwarding shim: pre-refactor hook entry path.

Sessions launched before the plugins/ -> harness/impl move captured this old
path in their hook config and cache it for the process lifetime; deleting the
file mid-session blocks every hook delivery. This shim forwards to the moved
entry so those sessions keep working. New sessions use the new path from
~/.claude/settings.json; this shim can be deleted once old sessions are gone.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from harness.impl.claude_code.hooks.entry import main  # noqa: E402


if __name__ == "__main__":
    main()
