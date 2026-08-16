"""Codex hook entry: a thin client of the daemon's hook endpoint.

Ships the exact stdin bytes plus the flat header values it can observe (the
terminal window, the CLI pid from its own ancestry) to
POST /api/harnesses/codex/hooks. All parsing and recording happen daemon-side
in `harness/impl/codex/hooks/gateway.py`.
"""

from __future__ import annotations

import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from harness.hooks import client as hook_client
from harness.impl.codex.hooks.gateway import CLI_PROCESS_NAME, HARNESS


def main() -> None:
    hook_client.run(HARNESS, CLI_PROCESS_NAME)


if __name__ == "__main__":
    main()
