"""Codex hook entry: a thin client of the daemon's hook endpoint.

Ships the exact stdin bytes plus the flat header values it can observe (the
terminal window, the CLI pid from its own ancestry) to
POST /api/harnesses/codex/hooks. All parsing and recording happen daemon-side
in `plugins/codex/hooks.py`.
"""

from __future__ import annotations

import os
import sys

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app import hook_client
from plugins.codex.hooks import CLI_PROCESS_NAME, HARNESS


def main() -> None:
    hook_client.run(HARNESS, CLI_PROCESS_NAME)


if __name__ == "__main__":
    main()
