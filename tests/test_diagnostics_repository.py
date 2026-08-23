"""Structured pipeline signoff checks."""

from __future__ import annotations

from audit.models import ApplicationErrorRecord
from domain.ids import ActorId, HarnessName, RawEventId, SessionId
from harness.models import RawEvent, TranslationResult
from repository.impl.sqlite.audit import SqliteAuditWriteRepository
from repository.impl.sqlite.canonical_events import SqliteCanonicalEventRepository
from repository.impl.sqlite.databases import audit_database, main_database
from repository.impl.sqlite.diagnostics import SqliteDiagnosticsRepository
from repository.impl.sqlite.raw_events import SqliteRawEventRepository


def raw_event(index: int) -> RawEvent:
    return RawEvent(
        raw_event_id=RawEventId(f"raw-{index}"),
        harness=HarnessName("codex"),
        source_type="rollout",
        source_name="rollout.jsonl",
        source_position=str(index),
        session_id=SessionId("session-one"),
        actor_id=ActorId("lead-one"),
        parent_actor_id=None,
        observed_at=float(index),
        encoding="json",
        payload=f'{{"index": {index}}}'.encode(),
        source_identity="rollout-one",
    )


def test_diagnostics_report_every_problem_in_the_requested_ranges(tmp_path):
    main = main_database(str(tmp_path / "main.db"))
    audit = audit_database(str(tmp_path / "audit.db"))
    raw_events = SqliteRawEventRepository(main)
    canonical = SqliteCanonicalEventRepository(main)
    events = [raw_event(index) for index in range(1, 4)]
    raw_events.record(events)
    canonical.record_translation(
        events[0], "1", TranslationResult((), "ignored_nonsemantic"), 4.0
    )
    canonical.record_translation(
        events[1], "1", TranslationResult((), "ignored_unknown", "new record"), 5.0
    )
    SqliteAuditWriteRepository(audit).record_error(
        ApplicationErrorRecord(
            SessionId("session-one"), "hook.py", "delivery", "trace", "bad input", 7, 6.0
        )
    )
    diagnostics = SqliteDiagnosticsRepository(main, audit)

    checkpoint = diagnostics.checkpoint()
    report = diagnostics.report(
        after_raw_event=1,
        through_raw_event=checkpoint.raw_event_cursor,
        after_audit_error=0,
        through_audit_error=checkpoint.audit_error_cursor,
    )

    assert checkpoint.pending_raw_event_count == 1
    assert (report.raw_event_count, report.verdict_count) == (2, 1)
    assert [problem.decision for problem in report.interpretation_problems] == [
        "ignored_unknown",
        None,
    ]
    assert report.interpretation_problems[0].reason == "new record"
    assert [problem.context for problem in report.audit_problems] == ["bad input"]

