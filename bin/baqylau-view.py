#!/usr/bin/env python3
"""Toggle an immutable canonical content view in the terminal mirror."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import terminal_views

SCHEME = "baqylau-view://"


def main(arguments: list[str]) -> None:
    if len(arguments) != 2 or not arguments[1].startswith(SCHEME):
        raise SystemExit("usage: baqylau-view.py baqylau-view://CONTENT_REFERENCE")
    terminal_views.toggle(arguments[1][len(SCHEME):])


if __name__ == "__main__":
    main(sys.argv)
