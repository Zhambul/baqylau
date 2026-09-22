# Copyright (c) 2026 Zhambyl Yermagambet
"""Build real history rows before the mixed interpretation writer is available."""

from dataclasses import astuple
from pathlib import Path

from baqylau_extension_api.models.scopes import InstallationScope

from domain.event_base import CanonicalEvent, EventPayload
from extensions.models.observations import ExtensionObservationMetadata
from repository.impl.sqlite.connection import SqliteDatabase
from repository.mapper.facts import canonical_event_insert_row
from tests import sqlite_migration_events as events
from tests.extension_api import samples

PREVIOUS_VERSION = 30
TARGET_VERSION = 31
CANDIDATE = "candidate-one"
COMPLETED_AT = 2000.0
DIGEST_LENGTH = 64
SCHEMA_DIGEST = "a" * DIGEST_LENGTH
CORE_COLUMNS = (
    "event_id, schema_version, event_type, session_id, actor_id, turn_id, parent_actor_id, harness, "
    "occurred_at, terminal_window_id, harness_process_id, accepted_at, payload"
)
EXTENSION_METADATA = (
    '{"scope":{"kind":"installation"},"schema_ref":'
    '{"owner":"test.reader","name":"text","version":1,"digest":"' + SCHEMA_DIGEST + '"},"causes":[]}'
)


def previous(directory: Path, *, populated: bool = True) -> SqliteDatabase:
    """Use the complete schema saved before the history migration was written.

    Returns:
        An independent schema-30 database.

    """
    path = Path(__file__).with_name("fixtures") / "main-schema-30.sql"
    schema = path.read_text(encoding="utf-8")
    database = SqliteDatabase(str(directory / "main.db"), schema, PREVIOUS_VERSION)
    database.initialize()
    if populated:
        events.populate(database)
    return database


def insert_core(database: SqliteDatabase, event: CanonicalEvent[EventPayload], history: str = CANDIDATE) -> int:
    """Insert a checked core body without invoking live application reactions.

    Returns:
        The database-assigned arrival cursor.

    """
    insert_row = astuple(canonical_event_insert_row(event, COMPLETED_AT))
    with database.write() as connection:
        connection.execute("INSERT OR IGNORE INTO canonical_histories VALUES(?, 2000.0)", (history,))
        cursor = connection.execute(
            f"INSERT INTO canonical_events({CORE_COLUMNS}, history_revision) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (*insert_row, history),
        ).lastrowid
    assert cursor is not None
    return cursor


def insert_extension(database: SqliteDatabase, event_id: str, history: str = "default") -> None:
    """Exercise the SQL branch without claiming schema-checked application acceptance."""
    with database.write() as connection:
        connection.execute("INSERT OR IGNORE INTO canonical_histories VALUES(?, 2000.0)", (history,))
        connection.execute(
            "INSERT INTO canonical_events(event_id, event_type, accepted_at, payload, history_revision, "
            "extension_metadata) VALUES(?, 'test.reader.created', 2000.0, ?, ?, ?)",
            (event_id, ' \n "exact é text"\r\n', history, EXTENSION_METADATA),
        )


def candidate_verdict(database: SqliteDatabase, raw_id: str, event_id: str) -> None:
    """Keep an alternate verdict and link beside the existing live interpretation."""
    with database.write() as connection:
        connection.execute(
            "INSERT INTO interpretations(raw_event_id, translator_version, decision, completed_at, "
            "history_revision, runtime_revision) "
            "VALUES(?, 'candidate-translator', 'translated', 2000.0, ?, 'runtime-2')",
            (raw_id, CANDIDATE),
        )
        connection.execute(
            "INSERT INTO interpretation_events VALUES(?, ?, 0, 'accepted', ?)", (event_id, raw_id, CANDIDATE),
        )


def insert_extension_raw(database: SqliteDatabase) -> None:
    """Retain a typed schema-30 extension original across the canonical rebuild."""
    metadata = ExtensionObservationMetadata(
        observation_key="input-one", scope=InstallationScope(), schema_ref=samples.schema_definition().reference,
        occurred_at=None, causes=(), runtime_revision="runtime-before",
    )
    with database.write() as connection:
        connection.execute(
            "INSERT INTO raw_events(raw_event_id, source_type, source_identity, source_name, source_position, "
            "observed_at, encoding, payload, extension_metadata) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("extension-input", "test.reader.line", "source-one", "source", "1", COMPLETED_AT, "json",
             b' "hello"\r\n', metadata.model_dump_json()),
        )
        connection.execute(
            "INSERT INTO pending_raw_events SELECT id, raw_event_id FROM raw_events "
            "WHERE raw_event_id='extension-input'",
        )
