# Copyright (c) 2026 Zhambyl Yermagambet
"""Provide test foundation audits."""

from __future__ import annotations

from tests import (
    canonical_foundation_components as foundation_components,
    foundation_dependencies,
    foundation_test_events,
    foundation_test_interpreter,
    foundation_test_liveness,
    foundation_test_primitives,
    foundation_test_reactions,
)
from tests.harness_names import CODEX_HARNESS

INITIAL_WATCHABLE_SESSIONS = 6

IGNORED_TRANSLATION = foundation_components.raw_events.TranslationResult(
    (), foundation_dependencies.domain.domain_records.RecordedTranslationDecision.IGNORED_NONSEMANTIC,
)
FIXTURE_EVENT_TIME = 10.0
SESSION_ID_TEXT = "session-one"
LEAD_ACTOR_ID_TEXT = "actor-lead"
HARNESS_VERSION = "1.0"
PRIMARY_SESSION = foundation_dependencies.domain.domain_ids.SessionId(SESSION_ID_TEXT)
FINISH_RAW_EVENT_ID = "raw-finish"
STALE_PROCESS_ID = 4194305
SAME_WINDOW_PROCESS_ID = 4194306
RESUMED_WINDOW_PROCESS_ID = 4194307
THIRD_SESSION_ID_TEXT = "session-3"
WINDOW_ID_TEXT = "window-one"


def test_accepted_session_finish_releases(database_path: str) -> None:
    """Verify an accepted session finish releases translator memory."""
    finish = foundation_dependencies.domain.event_base.CanonicalEvent(
        foundation_dependencies.domain.domain_ids.CanonicalEventId("finish-one"),
        foundation_dependencies.domain.domain_ids.SessionId(SESSION_ID_TEXT),
        foundation_dependencies.domain.domain_ids.ActorId(LEAD_ACTOR_ID_TEXT),
        None,
        None,
        CODEX_HARNESS,
        FIXTURE_EVENT_TIME,
        None,
        None,
        foundation_dependencies.domain.event_session.SessionFinished(
            foundation_dependencies.domain.outcomes.Outcome.SUCCEEDED, None,
        ),
    )
    plugin = foundation_test_reactions.example_plugin(
        foundation_components.raw_events.TranslationResult(
            (finish,), foundation_dependencies.domain.domain_records.RecordedTranslationDecision.TRANSLATED,
        ),
    )
    harnesses = foundation_dependencies.engine.harness_registry.HarnessRegistry()
    harnesses.register(plugin)
    runtime = foundation_test_liveness.build_interpreter(database_path, harnesses)
    runtime.sessions.save(
        CODEX_HARNESS, foundation_test_events.example_session(),
    )
    runtime.recorder.record((foundation_test_events.raw_observation(FINISH_RAW_EVENT_ID),))
    runtime.interpreter.tick()
    assert isinstance(plugin.translator, foundation_test_primitives.FixedTranslator)
    assert isinstance(plugin.sources, foundation_test_primitives.FixedSources)
    assert plugin.translator.released == [PRIMARY_SESSION]
    assert plugin.sources.released == [PRIMARY_SESSION]


def test_watchable_is_every_unfinished_session(
    database: foundation_dependencies.repository.SqliteDatabase,
    ignored_plugin: foundation_dependencies.engine.harness_contract.HarnessPlugin,
) -> None:
    """Verify watchable is every unfinished session without a count limit."""
    harnesses = foundation_dependencies.engine.harness_registry.HarnessRegistry()
    harnesses.register(ignored_plugin)
    sessions = foundation_dependencies.repository.SqliteSessionRepository(database, harnesses)
    store = foundation_dependencies.repository.SqliteCanonicalEventRepository(database)
    foundation_test_reactions.save_example_sessions(sessions, INITIAL_WATCHABLE_SESSIONS)
    assert len(sessions.watchable()) == INITIAL_WATCHABLE_SESSIONS
    assert all(session.plugin is not None for session in sessions.watchable())
    finish = foundation_dependencies.domain.event_base.CanonicalEvent(
        foundation_dependencies.domain.domain_ids.CanonicalEventId("finish-3"),
        foundation_dependencies.domain.domain_ids.SessionId(THIRD_SESSION_ID_TEXT),
        foundation_dependencies.domain.domain_ids.ActorId(LEAD_ACTOR_ID_TEXT),
        None,
        None,
        CODEX_HARNESS,
        FIXTURE_EVENT_TIME,
        None,
        None,
        foundation_dependencies.domain.event_session.SessionFinished(
            foundation_dependencies.domain.outcomes.Outcome.SUCCEEDED, None,
        ),
    )
    foundation_dependencies.repository.SqliteRawEventRepository(database).record((
        foundation_dependencies.standard.replace(
            foundation_test_events.raw_observation(FINISH_RAW_EVENT_ID),
            session_id=foundation_dependencies.domain.domain_ids.SessionId(THIRD_SESSION_ID_TEXT),
        ),
    ))
    store.record_translation(
        foundation_dependencies.standard.replace(
            foundation_test_events.raw_observation(FINISH_RAW_EVENT_ID),
            session_id=foundation_dependencies.domain.domain_ids.SessionId(THIRD_SESSION_ID_TEXT),
        ),
        HARNESS_VERSION,
        foundation_components.raw_events.TranslationResult(
            (finish,), foundation_dependencies.domain.domain_records.RecordedTranslationDecision.TRANSLATED,
        ),
        1.0,
    )
    watchable_ids = foundation_test_interpreter.watchable_session_ids(sessions)
    assert THIRD_SESSION_ID_TEXT not in watchable_ids
    assert len(watchable_ids) == INITIAL_WATCHABLE_SESSIONS - 1


def test_pid_less_session_is_loud_audited_error(
    database_path: str,
    ignored_plugin: foundation_dependencies.engine.harness_contract.HarnessPlugin,
) -> None:
    """Verify a pid less session is a loud audited error every tick.

    Never a silent skip: a session without a harness process id cannot be
        watched for liveness, and the failure lands in the audit until it can.
    """
    audited = foundation_test_events.RecordingAudit()
    harnesses = foundation_dependencies.engine.harness_registry.HarnessRegistry()
    harnesses.register(ignored_plugin)
    runtime = foundation_test_liveness.build_interpreter(database_path, harnesses, audit=audited)
    runtime.sessions.save(
        CODEX_HARNESS,
        foundation_dependencies.standard.replace(foundation_test_events.example_session(), harness_process_id=None),
    )
    runtime.interpreter.tick()
    assert audited.failures() == ["source construction"]


def test_dead_cli_process_becomes_one_session(
    database_path: str,
    ignored_plugin: foundation_dependencies.engine.harness_contract.HarnessPlugin,
) -> None:
    """The liveness source is the one finish signal every session has."""
    harnesses = foundation_dependencies.engine.harness_registry.HarnessRegistry()
    harnesses.register(ignored_plugin)
    runtime = foundation_test_liveness.build_interpreter(database_path, harnesses)
    runtime.sessions.save(
        CODEX_HARNESS,
        foundation_dependencies.standard.replace(
            foundation_test_events.example_session(), harness_process_id=STALE_PROCESS_ID,
        ),
    )
    runtime.interpreter.tick()
    events = runtime.store.page_from(0, 10)
    assert [type(stored.payload) for stored in events] == [foundation_dependencies.domain.event_session.SessionFinished]
    assert foundation_test_interpreter.stored_reason(events[0]) == "process_exited"
    assert not runtime.sessions.watchable()
    runtime.interpreter.tick()
    connection = foundation_dependencies.standard.sqlite3.connect(runtime.store.sqlite_database.path)
    assert connection.execute("SELECT count(*) FROM raw_events WHERE source_type='liveness'").fetchone()[0] == 1


def test_dead_run_finishes_before_its_rollout(
    database_path: str,
) -> None:
    """A stale large rollout cannot hold a newer session behind its exit."""
    rollout = foundation_test_events.raw_observation("stale-rollout")
    harnesses = foundation_dependencies.engine.harness_registry.HarnessRegistry()
    harnesses.register(
        foundation_test_reactions.example_plugin(
            foundation_components.raw_events.TranslationResult(
                (foundation_test_events.canonical_message(),),
                foundation_dependencies.domain.domain_records.RecordedTranslationDecision.TRANSLATED,
            ),
            sources=(foundation_test_primitives.FixedReadSource((rollout,)),),
        ),
    )
    runtime = foundation_test_liveness.build_interpreter(database_path, harnesses)
    runtime.sessions.save(
        CODEX_HARNESS,
        foundation_dependencies.standard.replace(
            foundation_test_events.example_session(),
            terminal_window_id=foundation_dependencies.domain.domain_ids.WindowId("stale-window"),
            harness_process_id=STALE_PROCESS_ID,
        ),
    )
    runtime.interpreter.tick()
    stored_events = runtime.store.page_from(0, 10)
    assert [type(event.payload) for event in stored_events] == [
        foundation_dependencies.domain.event_session.SessionFinished,
        foundation_dependencies.domain.event_conversation.MessageCreated,
    ]


def test_each_native_run_gets_its_own_process(
    ignored_plugin: foundation_dependencies.engine.harness_contract.HarnessPlugin,
) -> None:
    """A resumed session is one lineage but each terminal window is one run."""
    plugin = ignored_plugin
    first = foundation_test_reactions.finished_liveness_event(plugin, WINDOW_ID_TEXT, STALE_PROCESS_ID)
    same_run = foundation_test_reactions.finished_liveness_event(plugin, WINDOW_ID_TEXT, SAME_WINDOW_PROCESS_ID)
    resumed = foundation_test_reactions.finished_liveness_event(plugin, "window-two", RESUMED_WINDOW_PROCESS_ID)
    assert first.event_id == same_run.event_id
    assert resumed.event_id != first.event_id
    assert first.terminal_window_id == foundation_dependencies.domain.domain_ids.WindowId(WINDOW_ID_TEXT)
    assert resumed.terminal_window_id == foundation_dependencies.domain.domain_ids.WindowId("window-two")


def test_liveness_source_verifies_process() -> None:
    """Pids get reused by the OS; alive is not enough."""
    session = foundation_dependencies.standard.replace(
        foundation_test_events.example_session(), plugin=foundation_test_reactions.example_plugin(IGNORED_TRANSLATION),
    )
    alive = foundation_components.liveness.SessionLivenessSource(session, foundation_components.liveness.ProcessProbe())
    assert not alive.read(None)
    assert session.plugin is not None
    imposter = foundation_dependencies.standard.replace(
        session,
        plugin=foundation_dependencies.standard.replace(
            session.plugin,
            harness_info=foundation_dependencies.engine.HarnessInfo(
                CODEX_HARNESS,
                "Example",
                HARNESS_VERSION,
                foundation_dependencies.domain.domain_events.SCHEMA_VERSION,
                "definitely-not-us",
            ),
        ),
    )
    raw_events = foundation_components.liveness.SessionLivenessSource(
        imposter, foundation_components.liveness.ProcessProbe(),
    ).read(None)
    assert [raw.source_type for raw in raw_events] == [foundation_components.raw_events.LIVENESS_SOURCE_TYPE]
    with foundation_dependencies.standard.pytest.raises(ValueError, match="no harness process id"):
        foundation_components.liveness.SessionLivenessSource(
            foundation_dependencies.standard.replace(session, harness_process_id=None),
            foundation_components.liveness.ProcessProbe(),
        )
