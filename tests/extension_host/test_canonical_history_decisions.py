# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep intentional suppression and rejected core writes atomic in the current history."""

from pathlib import Path

import pytest

from domain.records import RecordedTranslationDecision
from harness.models.raw_events import TranslationResult
from repository.impl.sqlite import canonical_events, diagnostics, raw_event_audits, raw_events
from repository.impl.sqlite.connection import READ_ONLY_PRAGMAS, SqliteDatabase
from tests import sqlite_migration_fixture as snapshots, sqlite_test_fixtures as core
from tests.extension_host import canonical_history_fixture as fixtures


def test_suppressed_input_has_complete_verdict(main: SqliteDatabase) -> None:
    """Empty intentional output has an audit reason and does not remain pending."""
    raw = core.a_raw_event()
    raw_store = raw_events.SqliteRawEventRepository(main)
    raw_store.record((raw,))
    canonical_events.SqliteCanonicalEventRepository(main).record_translation(
        raw, "1", TranslationResult((), RecordedTranslationDecision.SUPPRESSED, "test drop"), fixtures.COMPLETED_AT,
    )
    audit = raw_event_audits.SqliteRawEventAuditRepository(main).audit(raw.raw_event_id)
    assert audit is not None and audit.interpretation is not None
    assert audit.interpretation.decision == RecordedTranslationDecision.SUPPRESSED
    assert audit.interpretation.reason == "test drop" and audit.interpretation.events == ()
    assert raw_store.unverdicted(10) == ()


def test_suppression_is_not_a_diagnostic_failure(main: SqliteDatabase, tmp_path: Path) -> None:
    """Diagnostics distinguish intentional suppression from an unknown or failed translation."""
    raw = core.a_raw_event()
    raw_events.SqliteRawEventRepository(main).record((raw,))
    canonical_events.SqliteCanonicalEventRepository(main).record_translation(
        raw, "1", TranslationResult((), RecordedTranslationDecision.SUPPRESSED, "test drop"), fixtures.COMPLETED_AT,
    )
    audit_database = SqliteDatabase(str(tmp_path / "absent-audit.db"), "", 1, READ_ONLY_PRAGMAS)
    report = diagnostics.SqliteDiagnosticsRepository(main, audit_database).report(
        after_raw_event=0, through_raw_event=1, after_audit_error=0, through_audit_error=0,
    )
    assert report.verdict_count == 1 and report.interpretation_problems == ()


def test_core_identity_conflict_keeps_pending(main: SqliteDatabase) -> None:
    """A core proposal cannot silently converge to an extension-owned fact with that ID."""
    event = core.a_started_event()
    raw = core.a_raw_event()
    raw_events.SqliteRawEventRepository(main).record((raw,))
    fixtures.insert_extension(main, event.event_id)
    before = snapshots.snapshot(main)
    with pytest.raises(ValueError, match="conflicts with an extension fact"):
        canonical_events.SqliteCanonicalEventRepository(main).record_translation(
            raw, "1", TranslationResult((event,), RecordedTranslationDecision.TRANSLATED), fixtures.COMPLETED_AT,
        )
    assert snapshots.snapshot(main) == before
