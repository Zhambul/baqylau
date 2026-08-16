#!/usr/bin/env python3
"""Terminal pane keybinding entry.

A STABLE path for external configuration to name. The implementation lives at
`terminal/panes/client.py`; this wrapper exists so a
future move inside the repository never means editing kitty.conf again.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from terminal.panes.client import main

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
