"""One connection policy and one schema for the canonical runtime database."""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

from domain.codec import SCHEMA_VERSION


class RuntimeDatabaseError(RuntimeError):
    pass


# The write split is the design: `sessions` is written only by the interpreter's
# session-upsert reaction (birth and upkeep derive from committed facts),
# `raw_events` by any recorder process, and the interpretation tables only by
# the interpreter. Positions are not stored anywhere separate: a pulled source
# resumes from the `source_position` of the last raw event carrying its
# `source_identity`, so recorded progress can never drift from the evidence.
SCHEMA = """
CREATE TABLE IF NOT EXISTS event_store_metadata(
    name TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions(
    session_id TEXT PRIMARY KEY,
    lead_actor_id TEXT NOT NULL,
    harness TEXT NOT NULL,
    harness_session_id TEXT NOT NULL,
    source_reference TEXT NOT NULL,
    working_directory TEXT,
    terminal_window_id TEXT,
    harness_process_id INTEGER,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS raw_events(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    raw_event_id TEXT NOT NULL UNIQUE,
    session_id TEXT NOT NULL,
    harness TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_identity TEXT NOT NULL,
    source_name TEXT NOT NULL,
    source_position TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    parent_actor_id TEXT,
    observed_at REAL NOT NULL,
    encoding TEXT NOT NULL,
    payload BLOB NOT NULL,
    terminal_window_id TEXT,
    harness_process_id INTEGER,
    account_id TEXT,
    account_display_name TEXT
);

CREATE INDEX IF NOT EXISTS index_raw_by_source
    ON raw_events(source_identity, id);

CREATE INDEX IF NOT EXISTS index_raw_by_session
    ON raw_events(session_id, observed_at);

CREATE TABLE IF NOT EXISTS operation_output(
    session_id TEXT NOT NULL,
    harness TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    parent_actor_id TEXT,
    source_path TEXT NOT NULL,
    chunk_source_type TEXT NOT NULL,
    delete_source INTEGER NOT NULL,
    initial_size INTEGER NOT NULL,
    initial_modified_at INTEGER NOT NULL,
    wait_for_source_change INTEGER NOT NULL,
    until TEXT NOT NULL CHECK(until IN ('operation_finished', 'session_finished')),
    state TEXT NOT NULL CHECK(state IN ('active', 'finishing')),
    created_at REAL NOT NULL,
    PRIMARY KEY(session_id, operation_id)
);

CREATE TABLE IF NOT EXISTS translation_records(
    raw_event_id TEXT PRIMARY KEY,
    translator_version TEXT NOT NULL,
    decision TEXT NOT NULL CHECK(
        decision IN ('translated', 'ignored_unknown', 'ignored_nonsemantic', 'translation_failed')
    ),
    reason TEXT,
    completed_at REAL NOT NULL,
    FOREIGN KEY(raw_event_id) REFERENCES raw_events(raw_event_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS canonical_events(
    cursor INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    schema_version INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    session_id TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    turn_id TEXT,
    parent_actor_id TEXT,
    harness TEXT NOT NULL,
    occurred_at REAL,
    terminal_window_id TEXT,
    harness_process_id INTEGER,
    accepted_at REAL NOT NULL,
    payload TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS index_canonical_session_type
    ON canonical_events(session_id, event_type, cursor);

CREATE INDEX IF NOT EXISTS index_canonical_session_actor
    ON canonical_events(session_id, actor_id, cursor);

CREATE INDEX IF NOT EXISTS index_canonical_session_cursor
    ON canonical_events(session_id, cursor);

CREATE TABLE IF NOT EXISTS canonical_provenance(
    event_id TEXT NOT NULL,
    raw_event_id TEXT NOT NULL,
    event_order INTEGER NOT NULL,
    storage_result TEXT NOT NULL CHECK(storage_result IN ('accepted', 'deduplicated')),
    PRIMARY KEY(event_id, raw_event_id),
    UNIQUE(raw_event_id, event_order),
    FOREIGN KEY(event_id) REFERENCES canonical_events(event_id) ON DELETE CASCADE,
    FOREIGN KEY(raw_event_id) REFERENCES raw_events(raw_event_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS session_application_state(
    session_id TEXT PRIMARY KEY,
    composer_text TEXT NOT NULL DEFAULT '',
    composer_origin TEXT NOT NULL DEFAULT '',
    composer_sequence REAL NOT NULL DEFAULT 0,
    queued_messages TEXT NOT NULL DEFAULT '[]',
    queue_origin TEXT NOT NULL DEFAULT '',
    dialog_attention_id TEXT,
    dialog_answers TEXT NOT NULL DEFAULT '[]',
    dialog_origin TEXT NOT NULL DEFAULT ''
);
"""


@contextmanager
def connect(database_path: str) -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(database_path, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA busy_timeout=10000")
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def initialize(database_path: str) -> str:
    """Create or verify the store; every runtime storage class calls this once."""
    database_path = os.path.abspath(database_path)
    directory = os.path.dirname(database_path)
    os.makedirs(directory, mode=0o700, exist_ok=True)
    with connect(database_path) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.executescript(SCHEMA)
        row = connection.execute(
            "SELECT value FROM event_store_metadata WHERE name='schema_version'"
        ).fetchone()
        if row is None:
            connection.execute(
                "INSERT INTO event_store_metadata(name, value) VALUES('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )
        elif row["value"] != str(SCHEMA_VERSION):
            raise RuntimeDatabaseError(f"unsupported event-store schema version: {row['value']}")
    os.chmod(database_path, 0o600)
    return database_path
