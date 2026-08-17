"""Where our files live on this machine — one answer, asked by everyone.

The event store, the preferences database, the uploads: all of them hang off
the one directory below. It used to be answered twice, by two modules reading
two different environment variables, which agreed only because both defaults
happened to match — set one and half the application moved while the other half
stayed. `BAQYLAU_DATA_DIRECTORY` is still honoured as the older spelling.

There are exactly TWO databases, and this module names both. `main.db` holds
everything the application owns and reads back; `audit.db` is separate because
every short-lived process in the tree writes it and it is what you read when
`main.db` is the suspect. There used to be a third, `locks.db`, holding the
daemon's pid claim — the port bind answers that question, so the file is gone.
"""

from __future__ import annotations

import os

MAIN_DATABASE_NAME = "main.db"
AUDIT_DATABASE_NAME = "audit.db"


def data_directory() -> str:
    """The durable directory: everything here survives a reboot."""
    configured = (
        os.environ.get("BAQYLAU_DATA_DIR")
        or os.environ.get("BAQYLAU_DATA_DIRECTORY")
        or "~/.local/share/baqylau"
    )
    return os.path.expanduser(configured)


def main_database_path() -> str:
    """Facts, evidence, preferences, terminal state, usage, uploads."""
    return os.path.join(data_directory(), MAIN_DATABASE_NAME)


def audit_database_path() -> str:
    """The operational audit: what the MACHINERY did."""
    return os.path.join(data_directory(), AUDIT_DATABASE_NAME)


