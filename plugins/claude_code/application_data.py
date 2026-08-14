"""Claude Code application-state paths shared by its short-lived processes."""

from __future__ import annotations

import os


def path(name: str) -> str:
    data_directory = os.environ.get("BAQYLAU_DATA_DIR") or os.path.expanduser(
        "~/.claude/baqylau"
    )
    return os.path.join(data_directory, "claude_code", name)
