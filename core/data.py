"""Where our files live on this machine — one answer, asked by everyone.

The event store, the preferences database, the uploads: all of them hang off
the one directory below. It used to be answered twice, by two modules reading
two different environment variables, which agreed only because both defaults
happened to match — set one and half the application moved while the other half
stayed. `BAQYLAU_DATA_DIRECTORY` is still honoured as the older spelling.

There are exactly THREE databases, and this module names all three. `main.db`
holds everything the application owns and reads back; `audit.db` is separate
because every short-lived process in the tree writes it and it is what you read
when `main.db` is the suspect; `locks.db` lives in the RUNTIME directory because
a pid claim surviving a reboot would name a pid that has since been reused.
"""

from __future__ import annotations

import os

MAIN_DATABASE_NAME = "main.db"
AUDIT_DATABASE_NAME = "audit.db"
LOCK_DATABASE_NAME = "locks.db"


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


def main_database_path() -> str:
    """Facts, evidence, preferences, terminal state, usage, uploads."""
    return os.path.join(data_directory(), MAIN_DATABASE_NAME)


def audit_database_path() -> str:
    """The operational diagnostics: what the MACHINERY did."""
    return os.path.join(data_directory(), AUDIT_DATABASE_NAME)


def lock_database_path() -> str:
    """Pid-liveness claims. Ephemeral by design — see the module docstring."""
    return os.path.join(runtime_directory(), LOCK_DATABASE_NAME)
