# core/clients.py — where the programs that run OUTSIDE this process live, and
# how we start one.
#
# `client/` holds every process the daemon does not own: the two panes, the
# terminal's key and click handlers, the hooks, the OTLP receiver, the status-line
# shim. They import nothing of ours, so this is the entire daemon-side half of
# the relationship — a directory, a path and an argv. WHICH file a launcher runs
# is named by the launcher (`terminal/adapter.py` for a pane, the telemetry
# launcher under its own plugin for the receiver), because the file
# belongs to the thing that starts it — and a shared package like this one may
# not name a harness anyway. What is single-sited here is the path arithmetic,
# which is what actually broke.
#
# The root is derived from a PACKAGE, not from a file's depth: `core` sits
# directly under the repository root by definition — if it did not, no import in
# this program would resolve — so this cannot drift the way a `parents[N]` count
# can, which is exactly how every pane process once died on startup.
from __future__ import annotations

from pathlib import Path
import sys

import core
from core.daemon.contract import HOST_ADDRESS, PORT_NUMBER

REPOSITORY_ROOT = Path(core.__file__).resolve().parent.parent
CLIENT_DIRECTORY = REPOSITORY_ROOT / "client"


def path(name: str) -> str:
    return str(CLIENT_DIRECTORY / name)


def command(name: str, *arguments: str | int) -> tuple[str, ...]:
    """The argv for a client WE launch: our own address first, always.

    A client cannot import where the daemon listens — it imports nothing of ours
    — so a launch that does not pass the address is a client that cannot answer.
    """
    return (
        sys.executable,
        path(name),
        HOST_ADDRESS,
        str(PORT_NUMBER),
        *(str(argument) for argument in arguments),
    )
