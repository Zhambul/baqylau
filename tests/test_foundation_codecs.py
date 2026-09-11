# Copyright (c) 2026 Zhambyl Yermagambet
"""Provide test foundation codecs."""

from __future__ import annotations

from tests import (
    canonical_foundation_components as foundation_components,
    foundation_dependencies,
    foundation_test_events,
    foundation_test_liveness,
    foundation_test_reactions,
)
from tests.harness_names import CODEX_HARNESS

SESSION_ID_TEXT = "session-one"
UPDATED_PROCESS_ID = 2
LEAD_ACTOR_ID_TEXT = "actor-lead"
SOURCE_FILE_NAME = "fixture.jsonl"
WORKING_DIRECTORY = "/work"
PRIMARY_SESSION = foundation_dependencies.domain.domain_ids.SessionId(SESSION_ID_TEXT)
FIXTURE_PROCESS_ID = 4242
SESSION_WINDOW_ID = "the-session-tab"


def test_codec_rejects_invalid_payload() -> None:
    """Verify codec rejects an invalid payload before storage."""
    event = foundation_test_events.canonical_message()
    invalid_payload = foundation_dependencies.standard.replace(event.payload, role="tool")
    with foundation_dependencies.standard.pytest.raises(
        foundation_components.documents.StoredDocumentError, match="role",
    ):
        foundation_dependencies.repository.mapper.canonical_event_insert_row(
            foundation_dependencies.standard.replace(event, payload=invalid_payload), 0,
        )


def test_stable_event_id_names_same_fact() -> None:
    """Verify stable event identifier names the same fact and distinguishes its phase."""
    identity = foundation_dependencies.domain.domain_ids.CanonicalEventIdentity(
        harness=CODEX_HARNESS,
        session_id=PRIMARY_SESSION,
        actor_id=foundation_dependencies.domain.domain_ids.ActorId(LEAD_ACTOR_ID_TEXT),
        subject_type="operation",
        subject_id="native-call",
        phase="started",
    )
    started = foundation_dependencies.domain.domain_ids.stable_event_id(identity)
    assert started == foundation_dependencies.domain.domain_ids.stable_event_id(identity)
    assert started != foundation_dependencies.domain.domain_ids.stable_event_id(
        foundation_dependencies.standard.replace(identity, phase="finished"),
    )
    assert started != foundation_dependencies.domain.domain_ids.stable_event_id(
        foundation_dependencies.standard.replace(
            identity, actor_id=foundation_dependencies.domain.domain_ids.ActorId("actor-child"),
        ),
    )


def test_evidence_translates_before_any_session(
    database: foundation_dependencies.repository.SqliteDatabase,
) -> None:
    """Facts may precede the session: there is no registration gate on the queue."""
    recorder = foundation_dependencies.repository.SqliteRawEventRepository(database)
    recorder.record((foundation_test_events.raw_observation("raw-early"),))
    backlog = recorder.unverdicted(10)
    assert [raw.raw_event_id for raw in backlog] == [foundation_dependencies.domain.domain_ids.RawEventId("raw-early")]


def test_session_is_born_by_reaction_to_its_own(
    database_path: str,
) -> None:
    """Verify the session is born by the reaction to its own started fact.

    The whole point: nothing registers a session. Its first delivery
        translates into `session.started`, and the upsert reaction derives the row
        — identity from the payload, location from the envelope.
    """
    session_start_harnesses = foundation_dependencies.engine.harness_registry.HarnessRegistry()
    started = foundation_test_events.session_started_event(
        terminal_window_id=foundation_dependencies.domain.domain_ids.WindowId(SESSION_WINDOW_ID),
        harness_process_id=FIXTURE_PROCESS_ID,
    )
    session_start_harnesses.register(
        foundation_test_reactions.example_plugin(
            foundation_components.raw_events.TranslationResult(
                (started,), foundation_dependencies.domain.domain_records.RecordedTranslationDecision.TRANSLATED,
            ),
        ),
    )
    runtime = foundation_test_liveness.build_interpreter(database_path, session_start_harnesses)
    runtime.recorder.record((foundation_test_events.raw_observation("raw-announcing"),))
    assert runtime.sessions.find(PRIMARY_SESSION) is None
    runtime.interpreter.tick()
    born = runtime.sessions.find(PRIMARY_SESSION)
    assert born is not None
    assert (
        born.source_reference,
        born.working_directory,
        born.project_directory,
        born.terminal_window_id,
        born.harness_process_id,
        born.plugin,
    ) == (
        SOURCE_FILE_NAME,
        WORKING_DIRECTORY,
        WORKING_DIRECTORY,
        SESSION_WINDOW_ID,
        FIXTURE_PROCESS_ID,
        session_start_harnesses.plugin(CODEX_HARNESS),
    )
    assert not runtime.recorder.unverdicted(10)


def test_facts_before_started_fact_commit(database_path: str) -> None:
    """Verify facts before the started fact commit but birth no session."""
    harnesses = foundation_dependencies.engine.harness_registry.HarnessRegistry()
    harnesses.register(
        foundation_test_reactions.example_plugin(
            foundation_components.raw_events.TranslationResult(
                (foundation_test_events.canonical_message(),),
                foundation_dependencies.domain.domain_records.RecordedTranslationDecision.TRANSLATED,
            ),
        ),
    )
    runtime = foundation_test_liveness.build_interpreter(database_path, harnesses)
    runtime.recorder.record((foundation_test_events.raw_observation("raw-early"),))
    runtime.interpreter.tick()
    assert runtime.sessions.find(PRIMARY_SESSION) is None
    assert len(runtime.store.page_from(cursor=0, limit=10)) == 1


def test_later_delivery_updates_live_columns(
    database: foundation_dependencies.repository.SqliteDatabase,
) -> None:
    """Verify a later delivery updates the live columns of the row.

    A resumed session shows up in a new window with a new process; the
        envelope of any later hook-borne fact refreshes the live columns.
    """
    sessions = foundation_dependencies.repository.SqliteSessionRepository(database)
    sessions.save(
        CODEX_HARNESS,
        foundation_dependencies.standard.replace(
            foundation_test_events.example_session(),
            terminal_window_id=foundation_dependencies.domain.domain_ids.WindowId("old-window"),
            harness_process_id=1,
        ),
    )
    reaction = foundation_components.reactions.SessionUpsertCanonicalEventReaction(sessions)
    reaction.react(
        foundation_dependencies.standard.replace(
            foundation_test_events.canonical_message(),
            terminal_window_id=foundation_dependencies.domain.domain_ids.WindowId("new-window"),
            harness_process_id=UPDATED_PROCESS_ID,
        ),
    )
    updated = sessions.find(PRIMARY_SESSION)
    assert updated is not None
    assert (updated.terminal_window_id, updated.harness_process_id, updated.project_directory) == (
        "new-window", UPDATED_PROCESS_ID, WORKING_DIRECTORY,
    )
    reaction.react(foundation_test_events.canonical_message())
    untouched = sessions.find(PRIMARY_SESSION)
    assert untouched is not None
    assert untouched.terminal_window_id == "new-window"


def test_file_start_does_not_erase_pid_from_hook(
    database: foundation_dependencies.repository.SqliteDatabase,
) -> None:
    """Verify a file start does not erase the pid from the hook start."""
    sessions = foundation_dependencies.repository.SqliteSessionRepository(database)
    sessions.save(
        CODEX_HARNESS,
        foundation_dependencies.standard.replace(
            foundation_test_events.example_session(),
            terminal_window_id=foundation_dependencies.domain.domain_ids.WindowId("session-window"),
            harness_process_id=FIXTURE_PROCESS_ID,
        ),
    )
    reaction = foundation_components.reactions.SessionUpsertCanonicalEventReaction(sessions)
    reaction.react(foundation_test_events.session_started_event())
    updated = sessions.find(PRIMARY_SESSION)
    assert updated is not None
    assert updated.terminal_window_id == "session-window"
    assert updated.harness_process_id == FIXTURE_PROCESS_ID
