# Copyright (c) 2026 Zhambyl Yermagambet
"""Provide test foundation interpretation."""

from __future__ import annotations

from tests import (
    canonical_foundation_components as foundation_components,
    foundation_dependencies,
    foundation_test_events,
    foundation_test_liveness,
    foundation_test_output,
    foundation_test_primitives,
    foundation_test_reactions,
    foundation_test_sources,
)
from tests.harness_names import CODEX_HARNESS

MAIN_DATABASE_NAME = "main.db"
FIXTURE_SOURCE_IDENTITY = "fixture:source"
SESSION_ID_TEXT = "session-one"
PRIMARY_SESSION = foundation_dependencies.domain.domain_ids.SessionId(SESSION_ID_TEXT)
FIRST_RAW_EVENT_ID = "raw-one"
SECOND_RAW_EVENT_ID = "raw-two"
SECOND_SESSION_ID_TEXT = "session-two"


def test_raw_event_audit_cli_prints_exact_raw(
    tmp_path: foundation_dependencies.standard.Path,
    monkeypatch: foundation_dependencies.standard.pytest.MonkeyPatch,
    capsys: foundation_dependencies.standard.pytest.CaptureFixture[str],
) -> None:
    """Verify raw event audit CLI prints exact raw and canonical correlation."""
    data_directory = tmp_path / "data"
    recorder = foundation_dependencies.repository.SqliteRawEventRepository(
        foundation_dependencies.repository.main_database(str(data_directory / MAIN_DATABASE_NAME)),
    )
    store = foundation_dependencies.repository.SqliteCanonicalEventRepository(
        foundation_dependencies.repository.main_database(str(data_directory / MAIN_DATABASE_NAME)),
    )
    raw_event = foundation_test_events.raw_observation(FIRST_RAW_EVENT_ID, payload=b"exact bytes\n")
    recorder.record((raw_event,))
    store.record_translation(
        raw_event,
        "1",
        foundation_components.raw_events.TranslationResult(
            (foundation_test_events.canonical_message(),),
            foundation_dependencies.domain.domain_records.RecordedTranslationDecision.TRANSLATED,
        ),
        1.0,
    )
    monkeypatch.setenv("BAQYLAU_DATA_DIR", str(data_directory))
    assert foundation_dependencies.audit.raw_event_audit_main(["raw", FIRST_RAW_EVENT_ID]) == 0
    document = foundation_dependencies.standard.json.loads(capsys.readouterr().out)
    assert document["payload_base64"] == "ZXhhY3QgYnl0ZXMK"
    assert document["canonical"][0]["event"]["event_id"] == "event-message"


def test_pulled_source_resumes_from_its_last(
    database: foundation_dependencies.repository.SqliteDatabase,
) -> None:
    """Progress is derived from the evidence itself, so it can never drift."""
    recorder = foundation_dependencies.repository.SqliteRawEventRepository(database)
    assert recorder.latest_positions([FIXTURE_SOURCE_IDENTITY]).get(FIXTURE_SOURCE_IDENTITY) is None
    recorder.record((
        foundation_test_events.raw_observation(FIRST_RAW_EVENT_ID),
        foundation_dependencies.standard.replace(
            foundation_test_events.raw_observation(SECOND_RAW_EVENT_ID), source_position="42",
        ),
    ))
    assert recorder.latest_positions([FIXTURE_SOURCE_IDENTITY]).get(FIXTURE_SOURCE_IDENTITY) == "42"
    assert recorder.latest_positions(["someone:else"]).get("someone:else") is None


def test_raw_event_audit_shows_exact_raw(tmp_path: foundation_dependencies.standard.Path) -> None:
    """Verify raw event audit shows exact raw interpretation and canonical chain."""
    event = foundation_test_events.canonical_message()
    runtime = foundation_test_output.registered_runtime(
        tmp_path,
        foundation_components.raw_events.TranslationResult(
            (event,), foundation_dependencies.domain.domain_records.RecordedTranslationDecision.TRANSLATED,
        ),
    )
    raw = foundation_test_events.raw_observation(FIRST_RAW_EVENT_ID)
    runtime.recorder.record((raw,))
    runtime.interpreter.tick()
    audit = foundation_dependencies.repository.SqliteRawEventAuditRepository(runtime.store.sqlite_database).audit(
        raw.raw_event_id,
    )
    assert audit is not None
    assert audit.interpretation is not None
    recorded_event = audit.interpretation.events[0]
    assert (audit.raw_event.payload, audit.interpretation.decision) == (
        raw.payload,
        foundation_dependencies.domain.domain_records.RecordedTranslationDecision.TRANSLATED,
    )
    assert (
        recorded_event.event.event_id,
        recorded_event.event.actor_id,
        recorded_event.accepted_at > raw.observed_at,
        audit.interpretation.completed_at,
        recorded_event.storage_result,
    ) == (event.event_id, event.actor_id, True, recorded_event.accepted_at, "accepted")
    assert foundation_dependencies.repository.SqliteRawEventAuditRepository(
        runtime.store.sqlite_database,
    ).audits_for_session(PRIMARY_SESSION) == (audit,)


def test_raw_event_audit_shows_uninterpreted(
    database: foundation_dependencies.repository.SqliteDatabase,
) -> None:
    """Verify raw event audit shows the uninterpreted backlog."""
    recorder = foundation_dependencies.repository.SqliteRawEventRepository(database)
    store = foundation_dependencies.repository.SqliteCanonicalEventRepository(database)
    recorder.record((foundation_test_events.raw_observation("raw-waiting"),))
    audit = foundation_dependencies.repository.SqliteRawEventAuditRepository(store.sqlite_database).audit(
        foundation_dependencies.domain.domain_ids.RawEventId("raw-waiting"),
    )
    assert audit is not None
    assert audit.interpretation is None


def test_interpreter_pulls_translates_and_commits(
    database_path: str,
) -> None:
    """Verify the interpreter pulls translates and commits in one tick."""
    event = foundation_test_events.canonical_message()
    raw_event = foundation_test_events.raw_observation("synthetic-raw")
    harnesses = foundation_dependencies.engine.harness_registry.HarnessRegistry()
    harnesses.register(
        foundation_test_reactions.example_plugin(
            foundation_components.raw_events.TranslationResult(
                (event,), foundation_dependencies.domain.domain_records.RecordedTranslationDecision.TRANSLATED,
            ),
            (foundation_test_primitives.FixedReadSource((raw_event,)),),
        ),
    )
    runtime = foundation_test_liveness.build_interpreter(database_path, harnesses)
    runtime.sessions.save(
        CODEX_HARNESS, foundation_test_events.example_session(),
    )
    runtime.interpreter.tick()
    committed = runtime.store.page_from(0, 10)
    assert [committed_event.event_id for committed_event in committed] == [event.event_id]
    assert committed[0].payload == event.payload


def test_one_failing_source_neither_stops_its(
    database_path: str,
) -> None:
    """The interpreter drives every pulled source and nothing restarts it.

    An unguarded exception here once killed observation for EVERY session silently: the
    conversation stopped arriving while hooks (separate processes) kept flowing, so the
    session still looked alive.
    """
    audited = foundation_test_events.RecordingAudit()
    harnesses = foundation_dependencies.engine.harness_registry.HarnessRegistry()
    harnesses.register(
        foundation_test_reactions.example_plugin(
            foundation_components.raw_events.TranslationResult(
                (foundation_test_events.canonical_message(),),
                foundation_dependencies.domain.domain_records.RecordedTranslationDecision.TRANSLATED,
            ),
            (
                foundation_test_sources.BrokenSource(),
                foundation_test_primitives.FixedReadSource((
                    foundation_test_events.raw_observation(FIRST_RAW_EVENT_ID),
                )),
            ),
        ),
    )
    runtime = foundation_test_liveness.build_interpreter(database_path, harnesses, audit=audited)
    runtime.sessions.save(
        CODEX_HARNESS, foundation_test_events.example_session(),
    )
    runtime.interpreter.tick()
    runtime.interpreter.tick()
    assert len(runtime.store.page_from(0, 10)) == 1
    assert audited.failures() == ["source read"]
    failure_context = audited.errors[0][1]
    assert isinstance(failure_context, foundation_dependencies.audit.FailureContext)
    assert failure_context.source_identity == "broken"


def test_one_pull_cycle_reads_resume_positions(
    database_path: str,
    monkeypatch: foundation_dependencies.standard.pytest.MonkeyPatch,
    ignored_plugin: foundation_dependencies.engine.harness_contract.HarnessPlugin,
) -> None:
    """Verify one pull cycle reads resume positions for all sessions at once."""
    harnesses = foundation_dependencies.engine.harness_registry.HarnessRegistry()
    harnesses.register(ignored_plugin)
    runtime = foundation_test_liveness.build_interpreter(database_path, harnesses)
    runtime.sessions.save(
        CODEX_HARNESS,
        foundation_test_events.example_session(SESSION_ID_TEXT),
    )
    runtime.sessions.save(
        CODEX_HARNESS,
        foundation_test_events.example_session(SECOND_SESSION_ID_TEXT),
    )
    real_latest_positions = runtime.recorder.latest_positions
    latest_positions_calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        runtime.recorder,
        "latest_positions",
        lambda source_identities: foundation_test_primitives.record_latest_positions(
            latest_positions_calls, real_latest_positions, source_identities,
        ),
    )
    runtime.interpreter.tick()
    assert len(latest_positions_calls) == 1
