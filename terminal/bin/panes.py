#!/usr/bin/env python3
"""Terminal pane keybinding entry.

The path the terminal's own keymap configuration names. It lives here,
inside the terminal concern, and carries no behaviour of its own: a sys.path
anchor, one import, one call. The implementation is
`terminal/panes/client.py`, free to move within this package without anyone
editing a config file.

Unlike a harness's hook path, this one is read at KEYPRESS time — but from the
terminal's in-memory config, so a moved file breaks the binding until the
terminal reloads its configuration.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from terminal.panes.client import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
