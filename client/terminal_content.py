#!/usr/bin/env python3
"""Copy one canonical content reference to the system clipboard.

    terminal_content.py baqylau-content://CONTENT_REFERENCE

The terminal's open-actions configuration names this file for the
`baqylau-content` protocol. A thin client of the daemon's existing content
resource — the click handler resolves nothing itself, and a daemon that does not
answer means nothing was copied.
"""

from __future__ import annotations

import os
import subprocess
import sys
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))  # my own directory

import _daemon                                                   # noqa: E402
import _wire                                                     # noqa: E402

SCHEME = "baqylau-content://"
CLIPBOARD_COMMAND = ("pbcopy",)


def main(arguments: list[str]) -> int:
    if len(arguments) != 1 or not arguments[0].startswith(SCHEME):
        print("usage: terminal_content.py baqylau-content://CONTENT_REFERENCE", file=sys.stderr)
        return 2
    reference = urllib.parse.quote(arguments[0][len(SCHEME):], safe="")
    content = _daemon.get(_wire.CONTENT_PATH % reference)
    if content is None:
        return 0                                # the daemon said nothing; copy nothing
    subprocess.run(CLIPBOARD_COMMAND, input=content, check=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
