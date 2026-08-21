#!/usr/bin/env python3
"""Copy what a mirror block holds to the system clipboard.

    terminal_content.py baqylau-content://SESSION/KIND/TARGET

The terminal's open-actions configuration names this file for the
`baqylau-content` protocol, so a click on a ⧉ link in the pane lands here.

Nothing is fetched. The pane holds every byte it draws — content is embedded in
the entries it was served — so it publishes the text behind its own links to a
local file and this program reads it (`client/_handoff.py`). A daemon that is
down changes nothing about a copy, which is the point: this is a frontend
gesture over data the frontend already has.
"""

from __future__ import annotations

import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))  # my own directory

import _handoff                                                  # noqa: E402

SCHEME = "baqylau-content://"
CLIPBOARD_COMMAND = ("pbcopy",)
USAGE = "usage: terminal_content.py baqylau-content://SESSION/KIND/TARGET"


def main(arguments: list[str]) -> int:
    if len(arguments) != 1 or not arguments[0].startswith(SCHEME):
        print(USAGE, file=sys.stderr)
        return 2
    parts = arguments[0][len(SCHEME):].split("/", 2)
    if len(parts) != 3:
        print(USAGE, file=sys.stderr)
        return 2
    session_id, kind, name = parts
    text = _handoff.target(session_id, kind, name)
    if text is None:
        return 0                          # no pane, or a link it no longer draws
    subprocess.run(CLIPBOARD_COMMAND, input=text.encode("utf-8"), check=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
