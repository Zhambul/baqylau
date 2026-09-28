# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep unpublished history out of every current core fact reader."""

from dataclasses import replace
from pathlib import Path

from domain import event_session, outcomes
from domain.records import RecordedTranslationDecision
from harness.models.raw_events import TranslationResult
from repository.impl.sqlite import canonical_events, diagnostics, raw_event_audits, raw_events, sessions
from repository.impl.sqlite.connection import READ_ONLY_PRAGMAS, SqliteDatabase
from tests import (
    sqlite_migration_events as events,
    sqlite_migration_fixture as snapshots,
    sqlite_test_fixtures as core,
    storage_reads,
)
from tests.extension_host import canonical_history_fixture as fixtures

EARLIER_TIME = 500.0


def test_candidate_has_no_current_core_read(main: SqliteDatabase) -> None:
    """Current identity, session, and stream reads all exclude a candidate-only fact."""
    event = core.a_started_event()
    fixtures.insert_core(main, event)
    canonical = canonical_events.SqliteCanonicalEventRepository(main)
    assert canonical.find(event.event_id) is None
    assert canonical.session_ids() == ()
    assert storage_reads.page_from(canonical, 0, 10) == ()


def test_candidate_cannot_claim_live_identity(main: SqliteDatabase) -> None:
    """A candidate ID does not deduplicate the first live acceptance of that ID."""
    event = core.a_started_event()
    fixtures.insert_core(main, replace(event, occurred_at=EARLIER_TIME))
    raw = core.a_raw_event()
    raw_events.SqliteRawEventRepository(main).record((raw,))
    canonical = canonical_events.SqliteCanonicalEventRepository(main)
    result = canonical.record_translation(
        raw, "1", TranslationResult((event,), RecordedTranslationDecision.TRANSLATED), fixtures.COMPLETED_AT,
    )
    assert result.accepted == (event,) and not result.deduplicated
    assert storage_reads.page_from(canonical, 0, 10)[0].occurred_at == event.occurred_at


def test_candidate_verdict_is_not_a_live_audit(main: SqliteDatabase) -> None:
    """Equal IDs in two histories do not multiply facts or leak alternate decisions."""
    events.populate(main)
    audits = raw_event_audits.SqliteRawEventAuditRepository(main)
    before = audits.audits_for_session(core.SESSION)
    fixtures.insert_core(main, core.a_started_event())
    fixtures.candidate_verdict(main, core.FIRST_RAW_EVENT_ID, core.FIRST_CANONICAL_EVENT_ID)
    assert audits.audits_for_session(core.SESSION) == before
    assert audits.audit(core.a_raw_event().raw_event_id) == before[0]
    accepted = canonical_events.SqliteCanonicalEventRepository(main).find(core.a_started_event().event_id)
    assert accepted is not None and accepted.raw_event_ids == ("raw-one", "repeated")


def test_diagnostics_only_count_current_history(main: SqliteDatabase, tmp_path: Path) -> None:
    """Alternate decisions and high cursors cannot move the live diagnostic boundary."""
    events.populate(main)
    audit_database = SqliteDatabase(str(tmp_path / "absent-audit.db"), "", 1, READ_ONLY_PRAGMAS)
    store = diagnostics.SqliteDiagnosticsRepository(main, audit_database)
    before = store.checkpoint()
    report = store.report(after_raw_event=0, through_raw_event=4, after_audit_error=0, through_audit_error=0)
    fixtures.insert_core(main, core.a_started_event())
    fixtures.candidate_verdict(main, core.FIRST_RAW_EVENT_ID, core.FIRST_CANONICAL_EVENT_ID)
    assert store.checkpoint() == before
    assert store.report(after_raw_event=0, through_raw_event=4, after_audit_error=0, through_audit_error=0) == report


def test_session_insert_ignores_candidate(main: SqliteDatabase) -> None:
    """A session created after a candidate finish still starts from the current history."""
    payload = event_session.SessionFinished(outcomes.Outcome.SUCCEEDED, None)
    finished = replace(core.a_started_event(), payload=payload)
    fixtures.insert_core(main, finished)
    sessions.SqliteSessionRepository(main).save(core.HARNESS, core.a_session())
    with main.read() as connection:
        lifecycle = connection.execute("SELECT lifecycle FROM sessions").fetchone()[0]
    assert lifecycle == "running"
    fixtures.insert_core(main, finished, "default")
    with main.read() as connection:
        lifecycle = connection.execute("SELECT lifecycle FROM sessions").fetchone()[0]
    assert lifecycle == "finished"


def test_old_forensic_reads_do_not_migrate(tmp_path: Path) -> None:
    """The compatibility path reads old tables without creating views or changing the file."""
    previous = fixtures.previous(tmp_path)
    before = snapshots.snapshot(previous)
    forensic = SqliteDatabase(previous.path, "", fixtures.TARGET_VERSION, READ_ONLY_PRAGMAS)
    events.require_original(forensic)
    assert snapshots.snapshot(previous) == before


def test_extension_rows_do_not_enter_core_reads(main: SqliteDatabase) -> None:
    """The live history can contain extension rows without fake core identities."""
    events.populate(main)
    fixtures.insert_extension(main, "extension-one")
    canonical = canonical_events.SqliteCanonicalEventRepository(main)
    assert len(storage_reads.page_from(canonical, 0, 10)) == 1
    assert canonical.session_ids() == (core.SESSION,)
    assert canonical.find(core.a_started_event("extension-one").event_id) is None
