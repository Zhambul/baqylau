#!/usr/bin/env python3
"""Toggle an immutable canonical content view in the terminal mirror.

A thin client of the daemon's `/api/terminal/views` — the daemon owns the
open/closed state and its mirror models re-render on the change."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import daemon_client

SCHEME = "baqylau-view://"


def main(arguments: list[str]) -> None:
    if len(arguments) != 2 or not arguments[1].startswith(SCHEME):
        raise SystemExit("usage: baqylau-view.py baqylau-view://CONTENT_REFERENCE")
    try:
        status, payload = daemon_client.post_json(
            "/api/terminal/views",
            {"content_reference": arguments[1][len(SCHEME):]},
        )
    except OSError as error:
        raise SystemExit(str(error)) from error
    if status != 200:
        raise SystemExit(payload.get("error") or "terminal view toggle failed")


if __name__ == "__main__":
    main(sys.argv)
