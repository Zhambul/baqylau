#!/usr/bin/env python3
"""Copy one canonical content reference to the system clipboard.

A thin client of the daemon's existing `/api/content/<reference>` resource —
the click handler resolves nothing itself."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.daemon import client as daemon_client

SCHEME = "baqylau-content://"


def main(arguments: list[str]) -> None:
    if len(arguments) != 2 or not arguments[1].startswith(SCHEME):
        raise SystemExit("usage: terminal/bin/content.py baqylau-content://CONTENT_REFERENCE")
    content_reference = arguments[1][len(SCHEME):]
    try:
        content = daemon_client.get_text("/api/content/" + quote(content_reference, safe=""))
    except OSError as error:
        raise SystemExit(str(error)) from error
    subprocess.run(["pbcopy"], input=content.encode("utf-8"), check=True)


if __name__ == "__main__":
    main(sys.argv)
