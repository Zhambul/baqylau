"""Claude Code hook entry: a thin client of the daemon's hook endpoint.

Ships the exact stdin bytes (plus ENVIRONMENT_KEYS — the env only this process
can see) to POST /api/harnesses/claude_code/hooks and prints the reply. All
parsing and recording happen daemon-side in `plugins/claude_code/hooks.py`.
"""

from __future__ import annotations

import os
import sys

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app import hook_client
from plugins.claude_code.hooks import ENVIRONMENT_KEYS, HARNESS


def main() -> None:
    hook_client.run(HARNESS, ENVIRONMENT_KEYS)


if __name__ == "__main__":
    main()
