# Copyright (c) 2026 Zhambyl Yermagambet
"""Build a populated independent schema-32 database before source progress storage."""

import sqlite3
from pathlib import Path

import pytest

from extensions.models.interpretations import InterpretationCommit
from repository.impl.sqlite import (
    databases,
    extension_lifecycle_reads,
    interpretation_journal_writes,
    interpretation_reads,
    record_migration_copy,
)
from repository.impl.sqlite.connection import SqliteDatabase
from tests import sqlite_migration_events as events
from tests.extension_host import interpretation_fixture as interpretations

PREVIOUS_VERSION = 32
TARGET_VERSION = 33


def previous(directory: Path) -> SqliteDatabase:
    """Use saved old DDL with real core rows, mixed facts, a journal, and decoder state.

    Returns:
        The complete old database without a source table or a manual version downgrade.

    """
    path = Path(__file__).with_name("fixtures") / "main-schema-32.sql"
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(databases, "MAIN_SCHEMA", path.read_text(encoding="utf-8"))
        patch.setattr(databases, "MAIN_SCHEMA_VERSION", PREVIOUS_VERSION)
        # Seed only schema-32 data. Restore the new reader before the actual upgrade.
        patch.setattr(extension_lifecycle_reads, "latest_record", lambda _connection: None)
        patch.setattr(record_migration_copy, "discard_interrupted", lambda _connection: None)
        patch.setattr(interpretation_reads, "read_journal", lambda *_args, **_kwargs: None)
        patch.setattr(interpretation_journal_writes, "write_journal", _legacy_journal)
        case = interpretations.installed(directory)
        events.populate(case.store.database)
        case.store.record_interpretation(interpretations.proposal(case))
    return case.store.database


def _legacy_journal(connection: sqlite3.Connection, request: InterpretationCommit) -> None:
    """Write the schema-32 inline journal during the old-schema seed."""
    proposal = request.proposal
    binding = proposal.binding
    connection.execute(
        "INSERT INTO interpretations(raw_event_id, translator_version, decision, reason, completed_at, "
        "history_revision, runtime_revision) VALUES(?, ?, ?, ?, ?, ?, ?)",
        (binding.raw_event_id, proposal.translator_version, proposal.decision, proposal.reason, request.completed_at,
         binding.history_revision, binding.runtime_revision),
    )
    connection.execute(
        "INSERT INTO interpretation_journals(history_revision, raw_event_id, proposal) VALUES(?, ?, ?)",
        (binding.history_revision, binding.raw_event_id, proposal.model_dump_json()),
    )
    if binding.mode == "live":
        connection.execute("DELETE FROM pending_raw_events WHERE raw_event_id=?", (binding.raw_event_id,))
