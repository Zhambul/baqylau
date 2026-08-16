"""Claude Code hook entry: a thin client of the daemon's hook endpoint.

Ships the exact stdin bytes plus a few flat header values (the terminal window,
the CLI pid from its own ancestry, the shell-selected account, the launch-time
model/effort selections riding the inherited environment) to
POST /api/harnesses/claude_code/hooks and prints the reply. All parsing and
recording happen daemon-side in `plugins/claude_code/hooks.py`.
"""

from __future__ import annotations

import os
import sys

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app import hook_client
from plugins.claude_code import account
from plugins.claude_code.hooks import CLI_PROCESS_NAME, HARNESS
from plugins.claude_code.launcher import LAUNCH_EFFORT_VARIABLE, LAUNCH_MODEL_VARIABLE


def main() -> None:
    selected = account.current(os.environ)
    hook_client.run(
        HARNESS,
        CLI_PROCESS_NAME,
        selected["slug"],
        selected["label"],
        launch_model=os.environ.get(LAUNCH_MODEL_VARIABLE) or "",
        launch_effort=os.environ.get(LAUNCH_EFFORT_VARIABLE) or "",
    )


if __name__ == "__main__":
    main()
