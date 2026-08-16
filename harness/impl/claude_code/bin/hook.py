#!/usr/bin/env python3
"""Claude Code hook entry.

The path external configuration names (~/.claude/settings.json). It lives HERE, inside the harness
it belongs to, so everything about one agent tool is in one directory — and it
carries no behaviour of its own: a sys.path anchor, one import, one call. The
implementation is `harness/impl/claude_code/hooks/entry.py`, free to move
within this harness without anyone editing a config file.

A config path is captured by the harness at session start and cached for the
process lifetime, so deleting or renaming this file breaks every already-running
session that named it. Add the new path first, repoint the config, and remove
the old file only once those sessions have ended.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from harness.impl.claude_code.hooks.entry import main  # noqa: E402

if __name__ == "__main__":
    main()
