"""Canonical application data location."""

from __future__ import annotations

import os


def data_directory() -> str:
    return os.path.expanduser(
        os.environ.get("BAQYLAU_DATA_DIR") or "~/.local/share/baqylau"
    )
