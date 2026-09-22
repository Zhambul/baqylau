# Copyright (c) 2026 Zhambyl Yermagambet
"""Populate the main schema through the existing event repositories."""

from dataclasses import replace

from domain.records import RecordedTranslationDecision
from harness.models.raw_events import TranslationResult
from repository.impl.sqlite import canonical_events, raw_event_audits, raw_events, sessions, shell_output
from repository.impl.sqlite.connection import SqliteDatabase
from tests import sqlite_migration_fixture as fixtures, sqlite_test_fixtures as events, sqlite_test_migrations

RAW_BYTES = fixtures.SOURCE_BYTES * 1000


def populate(database: SqliteDatabase) -> None:
    """Store compressed input, repeated facts, ignored input, and pending work."""
    observations = (
        replace(events.a_raw_event(), payload=RAW_BYTES),
        events.a_raw_event("repeated", "2"),
        events.a_raw_event("ignored", "3"),
        events.a_raw_event("pending", "4"),
    )
    raw_events.SqliteRawEventRepository(database).record(observations)
    sessions.SqliteSessionRepository(database).save(observations[0].harness, events.a_session())
    shell_output.SqliteShellOutputRepository(database).save(sqlite_test_migrations.a_following())
    canonical = canonical_events.SqliteCanonicalEventRepository(database)
    translation = TranslationResult((events.a_started_event(),), RecordedTranslationDecision.TRANSLATED)
    for observation in observations[:2]:
        canonical.record_translation(observation, "1", translation, observation.observed_at + 1)
    canonical.record_translation(
        observations[2], "1", TranslationResult((), RecordedTranslationDecision.IGNORED_NONSEMANTIC),
        observations[2].observed_at + 1,
    )


def require_original(database: SqliteDatabase) -> None:
    """Check decoded bytes, cursor order, provenance, and pending input after failure."""
    original = events.a_raw_event()
    raw = raw_events.SqliteRawEventRepository(database)
    assert raw.find(original.raw_event_id) == replace(original, payload=RAW_BYTES)
    assert raw.unverdicted(10) == (events.a_raw_event("pending", "4"),)
    assert raw.latest_positions((original.source_identity,)) == {original.source_identity: "4"}
    accepted = canonical_events.SqliteCanonicalEventRepository(database).page_from(0, 10)
    assert len(accepted) == 1 and accepted[0].cursor == 1
    audit = raw_event_audits.SqliteRawEventAuditRepository(database).audit(original.raw_event_id)
    assert audit is not None and audit.interpretation is not None
