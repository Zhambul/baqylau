"""Where our files live on this machine — one answer, asked by everyone.

The event store, the preferences database, the uploads: all of them hang off
the one directory below. It used to be answered twice, by two modules reading
two different environment variables, which agreed only because both defaults
happened to match — set one and half the application moved while the other half
stayed. `BAQYLAU_DATA_DIRECTORY` is still honoured as the older spelling.
"""

from __future__ import annotations

import os


def data_directory() -> str:
    """The durable directory: everything here survives a reboot."""
    configured = (
        os.environ.get("BAQYLAU_DATA_DIR")
        or os.environ.get("BAQYLAU_DATA_DIRECTORY")
        or "~/.local/share/baqylau"
    )
    return os.path.expanduser(configured)


def runtime_directory() -> str:
    """The ephemeral directory: locks and sockets, gone after a reboot."""
    return os.path.expanduser(os.environ.get("BAQYLAU_RUNTIME_DIRECTORY") or "/tmp")
