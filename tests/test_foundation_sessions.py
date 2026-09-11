# Copyright (c) 2026 Zhambyl Yermagambet
"""Provide test foundation sessions."""

from __future__ import annotations

from tests import (
    canonical_foundation_components as foundation_components,
    foundation_dependencies,
)
from tests.harness_names import CODEX_HARNESS

# Keep dependency modules separate from session test helpers.
# isort: split

from tests import (
    foundation_test_events,
    foundation_test_interpreter,
    foundation_test_liveness,
    foundation_test_output,
    foundation_test_primitives,
    foundation_test_reactions,
    foundation_test_sources,
)

IGNORED_TRANSLATION = foundation_components.raw_events.TranslationResult(
    (), foundation_dependencies.domain.domain_records.RecordedTranslationDecision.IGNORED_NONSEMANTIC,
)
HARNESS_VERSION = "1.0"
OWN_PROCESS_NAME = foundation_dependencies.standard.Path(
    foundation_dependencies.standard.subprocess.run(
        ["ps", "-o", "comm=", "-p", str(foundation_dependencies.standard.os.getpid())],
        capture_output=True,
        check=False,
        text=True,
    ).stdout.strip(),
).name
FIRST_RAW_EVENT_ID = "raw-one"
OLDER_OBSERVATION_TIME = 99.0
SECOND_ACCEPTANCE_TIME = 2.0
SECOND_RAW_EVENT_ID = "raw-two"


def test_interpretation_commits_verdict_canon(
    tmp_path: foundation_dependencies.standard.Path) -> None:
    """Verify interpretation commits verdict canonical and provenance together."""
    event = foundation_test_events.canonical_message()
    runtime = foundation_test_output.registered_runtime(
        tmp_path,
        foundation_components.raw_events.TranslationResult(
            (event,), foundation_dependencies.domain.domain_records.RecordedTranslationDecision.TRANSLATED,
        ),
    )
    runtime.recorder.record((foundation_test_reactions.first_raw_observation(),))
    runtime.interpreter.tick()
    assert not runtime.recorder.unverdicted(10)
    committed = runtime.store.page_from(cursor=0, limit=10)[0]
    assert foundation_dependencies.standard.replace(committed, cursor=None, accepted_at=None) == event
    connection = foundation_dependencies.standard.sqlite3.connect(runtime.store.sqlite_database.path)
    assert connection.execute("SELECT count(*) FROM raw_events").fetchone()[0] == 1
    assert (
        connection.execute("SELECT decision FROM interpretations").fetchone()[0]
        == foundation_dependencies.domain.domain_records.RecordedTranslationDecision.TRANSLATED
    )
    assert connection.execute("SELECT event_order, storage_result FROM interpretation_events").fetchone() == (
        0,
        "accepted",
    )


def test_replay_is_idempotent_and_second(tmp_path: foundation_dependencies.standard.Path) -> None:
    """Verify replay is idempotent and a second observation adds provenance."""
    event = foundation_test_events.canonical_message()
    runtime = foundation_test_output.registered_runtime(
        tmp_path,
        foundation_components.raw_events.TranslationResult(
            (event,), foundation_dependencies.domain.domain_records.RecordedTranslationDecision.TRANSLATED,
        ),
    )
    runtime.recorder.record((foundation_test_reactions.first_raw_observation(),))
    runtime.interpreter.tick()
    runtime.recorder.record((
        foundation_dependencies.standard.replace(
            foundation_test_events.raw_observation(FIRST_RAW_EVENT_ID), observed_at=OLDER_OBSERVATION_TIME,
        ),
    ))
    runtime.recorder.record((foundation_test_events.raw_observation(SECOND_RAW_EVENT_ID),))
    runtime.interpreter.tick()
    stored = runtime.store.page_from(cursor=0, limit=10)
    assert len(stored) == 1
    assert foundation_test_interpreter.provenance(runtime.store, stored[0]) == (
        foundation_dependencies.domain.domain_ids.RawEventId(FIRST_RAW_EVENT_ID),
        foundation_dependencies.domain.domain_ids.RawEventId(SECOND_RAW_EVENT_ID),
    )
    connection = foundation_dependencies.standard.sqlite3.connect(runtime.store.sqlite_database.path)
    assert (
        connection.execute("SELECT storage_result FROM interpretation_events WHERE raw_event_id='raw-two'").fetchone()[
            0
        ]
        == "deduplicated"
    )


def test_reused_raw_identity_is_corruption_not(
    database: foundation_dependencies.repository.SqliteDatabase,
) -> None:
    """Verify reused raw identity is corruption not convergence."""
    recorder = foundation_dependencies.repository.SqliteRawEventRepository(database)
    recorder.record((foundation_test_reactions.first_raw_observation(),))
    with foundation_dependencies.standard.pytest.raises(
        foundation_dependencies.repository.EventIdentityConflictError, match="raw event identity reused",
    ):
        recorder.record((foundation_test_events.raw_observation(FIRST_RAW_EVENT_ID, payload=b"different"),))


def test_re_observing_one_fact_is_idempotent_even(
    tmp_path: foundation_dependencies.standard.Path) -> None:
    """A canonical identity names a FACT, so re-observing it only audits the interpretation.

    Several sources legitimately converge on one event (a hook, the harness's
    own files, the foreground tee) and may render it differently. The first
    writer stays authoritative and the later rendering stays recoverable from
    its own raw evidence.
    """
    runtime = foundation_test_output.registered_runtime(tmp_path, IGNORED_TRANSLATION)
    runtime.recorder.record((
        foundation_test_events.raw_observation(FIRST_RAW_EVENT_ID),
        foundation_test_events.raw_observation(SECOND_RAW_EVENT_ID),
    ))
    runtime.store.record_translation(
        foundation_test_events.raw_observation(FIRST_RAW_EVENT_ID),
        HARNESS_VERSION,
        foundation_components.raw_events.TranslationResult(
            (foundation_test_events.canonical_message(),),
            foundation_dependencies.domain.domain_records.RecordedTranslationDecision.TRANSLATED,
        ),
        1.0,
    )
    converged = runtime.store.record_translation(
        foundation_test_events.raw_observation(SECOND_RAW_EVENT_ID),
        HARNESS_VERSION,
        foundation_components.raw_events.TranslationResult(
            (foundation_test_events.canonical_message(text="changed"),),
            foundation_dependencies.domain.domain_records.RecordedTranslationDecision.TRANSLATED,
        ),
        SECOND_ACCEPTANCE_TIME,
    )
    assert not converged.accepted
    assert [event.event_id for event in converged.deduplicated] == [
        foundation_dependencies.domain.domain_ids.CanonicalEventId("event-message"),
    ]
    stored = runtime.store.page_from(0, 10)
    assert len(stored) == 1
    assert (
        isinstance(stored[0].payload, foundation_dependencies.domain.event_conversation.MessageCreated)
        and isinstance(stored[0].payload.content, foundation_dependencies.domain.domain_content.TextContent)
    )
    connection = foundation_dependencies.standard.sqlite3.connect(runtime.store.sqlite_database.path)
    assert (
        stored[0].payload.content.text,
        foundation_test_interpreter.provenance(runtime.store, stored[0]),
        connection.execute("SELECT count(*) FROM raw_events WHERE raw_event_id='raw-two'").fetchone()[0],
    ) == (
        "hello",
        (
            foundation_dependencies.domain.domain_ids.RawEventId(FIRST_RAW_EVENT_ID),
            foundation_dependencies.domain.domain_ids.RawEventId(SECOND_RAW_EVENT_ID),
        ),
        1,
    )


def test_translation_cannot_move_raw_evidence(
    tmp_path: foundation_dependencies.standard.Path) -> None:
    """A translator that rewrites raw-event envelope fields gets a failure verdict."""
    event = foundation_test_events.canonical_message(actor_id="actor-child")
    runtime = foundation_test_output.registered_runtime(
        tmp_path,
        foundation_components.raw_events.TranslationResult(
            (event,), foundation_dependencies.domain.domain_records.RecordedTranslationDecision.TRANSLATED,
        ),
    )
    runtime.recorder.record((foundation_test_events.raw_observation("raw-child"),))
    runtime.interpreter.tick()
    connection = foundation_dependencies.standard.sqlite3.connect(runtime.store.sqlite_database.path)
    assert connection.execute("SELECT count(*) FROM canonical_events").fetchone()[0] == 0
    decision, reason = connection.execute(
        "SELECT decision, reason FROM interpretations WHERE raw_event_id='raw-child'",
    ).fetchone()
    assert decision == "translation_failed"
    assert "actor does not match" in reason


def test_translation_failure_is_complete_audited(
    tmp_path: foundation_dependencies.standard.Path) -> None:
    """Verify translation failure is a complete audited decision."""
    runtime = foundation_test_output.registered_runtime(
        tmp_path,
        foundation_components.raw_events.TranslationError("malformed record", context="line 1"),
    )
    runtime.recorder.record((foundation_test_events.raw_observation("raw-bad", payload=b"not json"),))
    runtime.interpreter.tick()
    connection = foundation_dependencies.standard.sqlite3.connect(runtime.store.sqlite_database.path)
    decision, reason = connection.execute(
        "SELECT decision, reason FROM interpretations WHERE raw_event_id='raw-bad'",
    ).fetchone()
    assert decision == "translation_failed"
    assert reason == "TranslationError: malformed record"
    assert connection.execute("SELECT count(*) FROM canonical_events").fetchone()[0] == 0


def test_translator_bug_becomes_verdict_and_never(
    database_path: str,
) -> None:
    """The backlog is ordered; an unverdicted row would block everything behind it."""
    harnesses = foundation_dependencies.engine.harness_registry.HarnessRegistry()
    harnesses.register(
        foundation_dependencies.engine.harness_contract.HarnessPlugin(
            harness_info=foundation_dependencies.engine.HarnessInfo(
                CODEX_HARNESS,
                "Example",
                HARNESS_VERSION,
                foundation_dependencies.domain.domain_events.SCHEMA_VERSION,
                OWN_PROCESS_NAME,
            ),
            sources=foundation_test_primitives.FixedSources(),
            translator=foundation_test_sources.BuggyTranslator(),
        ),
    )
    runtime = foundation_test_liveness.build_interpreter(database_path, harnesses)
    runtime.sessions.save(
        CODEX_HARNESS, foundation_test_events.example_session(),
    )
    runtime.recorder.record((foundation_test_events.raw_observation("raw-bug"),))
    runtime.interpreter.tick()
    connection = foundation_dependencies.standard.sqlite3.connect(runtime.store.sqlite_database.path)
    decision, reason = connection.execute(
        "SELECT decision, reason FROM interpretations WHERE raw_event_id='raw-bug'",
    ).fetchone()
    assert decision == "translation_failed"
    assert "ZeroDivisionError" in reason
    assert not runtime.recorder.unverdicted(10)
