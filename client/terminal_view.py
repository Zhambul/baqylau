#!/usr/bin/env python3
"""Toggle an immutable canonical content view in the terminal mirror.

    terminal_view.py baqylau-view://CONTENT_REFERENCE

The terminal's open-actions configuration names this file for the
`baqylau-view` protocol, so a click on a reference in the mirror lands here. The
daemon owns the open/closed state and its mirror models re-render on the change;
this resolves nothing itself.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))  # my own directory

import _daemon                                                   # noqa: E402
import _wire                                                     # noqa: E402

SCHEME = "baqylau-view://"


def main(arguments: list[str]) -> int:
    if len(arguments) != 1 or not arguments[0].startswith(SCHEME):
        print("usage: terminal_view.py baqylau-view://CONTENT_REFERENCE", file=sys.stderr)
        return 2
    _daemon.post_json(_wire.VIEW_PATH, {"content_reference": arguments[0][len(SCHEME):]})
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
