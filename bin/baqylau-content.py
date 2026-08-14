#!/usr/bin/env python3
"""Copy one canonical content reference to the system clipboard."""

from __future__ import annotations

import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.bootstrap import build_default_application

SCHEME = "baqylau-content://"


def main(arguments: list[str]) -> None:
    if len(arguments) != 2 or not arguments[1].startswith(SCHEME):
        raise SystemExit("usage: baqylau-content.py baqylau-content://CONTENT_REFERENCE")
    content_reference = arguments[1][len(SCHEME):]
    content = build_default_application().content.resolve(content_reference)
    subprocess.run(["pbcopy"], input=content.encode("utf-8"), check=True)


if __name__ == "__main__":
    main(sys.argv)
