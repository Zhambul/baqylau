# Copyright (c) 2026 Zhambyl Yermagambet
"""Read the exact stored raw payloads of the lifecycle daemon cases."""

import sqlite3

from harness.impl.claude_code.canonical import records as claude_records
from repository.impl.sqlite import databases
from repository.mapper.raw_payloads import restored
from tests.extension_host import lifecycle_daemon_fixture as fixture

DATABASE_NAME = "main.db"
ENCODING = "utf-8"


def stored_payloads(case: fixture.LifecycleDaemon) -> tuple[bytes, ...]:
    """Read every stored raw payload from a read-only private handle.

    Returns:
        The exact stored payload bytes in acceptance order.

    """
    database = databases.read_only(databases.main_database(str(case.directory / DATABASE_NAME)))
    with database.read() as connection:
        rows = connection.execute("SELECT payload, payload_codec FROM raw_events ORDER BY rowid").fetchall()
    return tuple(_restored_payload(row) for row in rows)


def require_payload(case: fixture.LifecycleDaemon, hook: claude_records.HookPayload) -> None:
    """Require that one delivery is the only stored payload, byte for byte."""
    assert stored_payloads(case) == (hook.model_dump_json().encode(ENCODING),)


def _restored_payload(row: sqlite3.Row) -> bytes:
    return restored(bytes(row["payload"]), str(row["payload_codec"]))
