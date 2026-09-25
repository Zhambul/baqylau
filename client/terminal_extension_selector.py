#!/usr/bin/env python3
# Copyright (c) 2026 Zhambyl Yermagambet
"""Choose an extension view for the current window and open it in a pane beside it.

A terminal key binding runs this as an overlay, for example in kitty:
`map ctrl+shift+e launch --type=overlay python3 client/terminal_extension_selector.py 127.0.0.1 8377`.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _daemon
import _extension_selector as selector
import _http
import _selector_keys

ARGUMENT_COUNT = 2


def main(arguments: list[str]) -> None:
    """Run the command."""
    if len(arguments) != ARGUMENT_COUNT:
        sys.exit("usage: terminal_extension_selector.py HOST PORT")
    options = _daemon.ConnectionOptions(arguments[0], int(arguments[1]))
    window_id = _http.window_id(os.environ)
    views = selector.read_views(options.host, options.port, window_id) or ()
    chosen = _selector_keys.choose(views)
    if chosen is not None:
        _open_view_pane(options, window_id, chosen)


def _open_view_pane(options: _daemon.ConnectionOptions, window_id: str, chosen: selector.AvailableView) -> None:
    body = selector.PaneOpen(
        extension_id=chosen.extension_id, view_id=chosen.view_id, scope=chosen.scope,
        window_id=window_id, working_directory=str(Path.cwd()),
    )
    _daemon.post(_http.EXTENSION_PANES_PATH, body.json_bytes(), options=options)


if __name__ == "__main__":
    main(sys.argv[1:])
