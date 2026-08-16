#!/usr/bin/env python3
"""Codex hook entry.

A STABLE path for external configuration to name. The implementation lives at
`harness/impl/codex/hooks/entry.py`; this wrapper exists so a
future move inside the repository never means editing ~/.codex/hooks.json again.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from harness.impl.codex.hooks.entry import main

if __name__ == "__main__":
    main()
