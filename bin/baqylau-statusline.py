#!/usr/bin/env python3
"""Claude Code status-line shim entry.

A STABLE path for external configuration to name. The implementation lives at
`harness/impl/claude_code/hooks/statusline.py`; this wrapper exists so a
future move inside the repository never means editing ~/.claude/settings.json again.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from harness.impl.claude_code.hooks.statusline import main

if __name__ == "__main__":
    main()
