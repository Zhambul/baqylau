# Copyright (c) 2026 Zhambyl Yermagambet
"""Provide test foundation sampling."""

from __future__ import annotations

from tests import (
    canonical_foundation_components as foundation_components,
    foundation_dependencies,
    foundation_test_events,
    foundation_test_output,
    foundation_test_primitives,
    foundation_test_sources,
)
from tests.harness_names import CODEX_HARNESS

SESSION_ID_TEXT = "session-one"
EXPECTED_TERMINAL_READS = 2
WORKING_DIRECTORY = "/work"
PRIMARY_SESSION = foundation_dependencies.domain.domain_ids.SessionId(SESSION_ID_TEXT)
FIXTURE_PROCESS_ID = 4242
ASSIGNMENT_ID_TEXT = "assignment-one"
MEDIA_TYPE_FIELD = "media_type"
TEXT_FIELD = "text"
type JsonValue = bool | float | int | str | list[JsonValue] | dict[str, JsonValue] | None


def test_terminal_snapshot_sampler_uses_one_read() -> None:
    """Verify terminal snapshot sampler uses one read per slow interval."""
    now: list[float] = [0]
    terminal = foundation_test_primitives.CountingTerminal()
    sampler = foundation_components.loop.TerminalSnapshotSampler(terminal, lambda: now[0])
    sampler.sample()
    now[0] = 0.9
    sampler.sample()
    assert terminal.calls == 1
    now[0] = 1.0
    sampler.sample()
    assert terminal.calls == EXPECTED_TERMINAL_READS


def test_terminal_snapshot_sampler_reads_again() -> None:
    """Verify terminal snapshot sampler reads again after a session starts."""
    now: list[float] = [0]
    terminal = foundation_test_primitives.CountingTerminal()
    sampler = foundation_components.loop.TerminalSnapshotSampler(terminal, lambda: now[0])
    sampler.sample()
    now[0] = 0.1
    sampler.invalidate()
    sampler.sample()
    assert terminal.calls == EXPECTED_TERMINAL_READS


def test_accepted_session_start_invalidates(tmp_path: foundation_dependencies.standard.Path) -> None:
    """Verify an accepted session start invalidates the terminal snapshot."""
    runtime = foundation_test_output.registered_runtime(
        tmp_path,
        foundation_components.raw_events.TranslationResult(
            (foundation_test_events.session_started_event(),),
            foundation_dependencies.domain.domain_records.RecordedTranslationDecision.TRANSLATED,
        ),
    )
    snapshots = foundation_test_sources.RecordingSnapshots()
    runtime.interpreter.terminal_snapshots = snapshots
    runtime.recorder.record((foundation_test_events.raw_observation("raw-start"),))
    runtime.interpreter.tick()
    assert len(runtime.store.page_from(cursor=0, limit=10)) == 1
    assert snapshots.invalidations == 1


@foundation_dependencies.standard.pytest.mark.parametrize(
    ("payload", "event_type", "expected_payload"),
    (
        (
            foundation_dependencies.domain.event_actor.ActorStarted(
                "weather researcher",
                foundation_dependencies.domain.messaging.ActorRole.CHILD,
            ),
            "actor.started",
            {"name": "weather researcher", "role": "child"},
        ),
        (
            foundation_dependencies.domain.event_actor.ActorAssignmentStarted(
                foundation_dependencies.domain.domain_ids.AssignmentId(ASSIGNMENT_ID_TEXT),
                foundation_dependencies.domain.domain_content.TextContent("Get Bali weather"),
                actor_name="researcher",
                prompt=foundation_dependencies.domain.domain_content.TextContent(
                    "Look up the weather in Bali.",
                    foundation_dependencies.domain.domain_content.MediaType.TEXT_MARKDOWN,
                ),
            ),
            "actor.assignment_started",
            {
                "assignment_id": ASSIGNMENT_ID_TEXT,
                "brief": {MEDIA_TYPE_FIELD: "text/plain", TEXT_FIELD: "Get Bali weather"},
                "actor_name": "researcher",
                "prompt": {
                    MEDIA_TYPE_FIELD: "text/markdown",
                    TEXT_FIELD: "Look up the weather in Bali.",
                },
            },
        ),
        (
            foundation_dependencies.domain.event_actor.ActorAssignmentFinished(
                foundation_dependencies.domain.domain_ids.AssignmentId(ASSIGNMENT_ID_TEXT),
                foundation_dependencies.domain.outcomes.Outcome.SUCCEEDED,
                foundation_dependencies.domain.domain_content.TextContent("Sunny"),
                None,
            ),
            "actor.assignment_finished",
            {
                "assignment_id": ASSIGNMENT_ID_TEXT,
                "outcome": "succeeded",
                "reason": None,
                "result": {MEDIA_TYPE_FIELD: "text/plain", TEXT_FIELD: "Sunny"},
            },
        ),
        (
            foundation_dependencies.domain.event_actor.ActorFinished("process exited"),
            "actor.finished",
            {"reason": "process exited"},
        ),
        (
            foundation_dependencies.domain.event_shell.ShellInputProvided(
                foundation_dependencies.domain.domain_ids.ShellId("operation-one"),
                foundation_dependencies.domain.domain_content.TextContent("yes\n"),
                closed=False,
            ),
            "shell.input_provided",
            {
                "shell_id": "operation-one",
                "content": {MEDIA_TYPE_FIELD: "text/plain", TEXT_FIELD: "yes\n"},
                "closed": False,
            },
        ),
    ),
)
def test_actor_lifecycle_payload_contract(
    payload: foundation_dependencies.domain.event_base.EventPayload,
    event_type: str,
    expected_payload: dict[str, JsonValue],
) -> None:
    """Verify actor lifecycle payload contract."""
    event = foundation_dependencies.domain.event_base.CanonicalEvent(
        event_id=foundation_dependencies.domain.domain_ids.CanonicalEventId("event-one"),
        session_id=PRIMARY_SESSION,
        actor_id=foundation_dependencies.domain.domain_ids.ActorId("actor-one"),
        turn_id=None,
        parent_actor_id=None,
        harness=CODEX_HARNESS,
        occurred_at=1.0,
        terminal_window_id=None,
        harness_process_id=None,
        payload=payload,
    )
    assert foundation_dependencies.domain.domain_events.EVENT_TYPES[type(payload)] == event_type
    assert (
        foundation_dependencies.standard.json.loads(foundation_dependencies.repository.mapper.payload_json(event))
        == expected_payload
    )
    assert "child" not in event_type


def test_repo_never_leaves_its_transaction_open(
    database: foundation_dependencies.repository.SqliteDatabase,
) -> None:
    """Verify a repository never leaves its transaction open."""
    with database.write() as connection:
        opened_connection = connection
        connection.execute("CREATE TABLE example(value TEXT)")
    assert not opened_connection.in_transaction
    with database.read() as reused_connection:
        assert reused_connection is opened_connection
        assert not reused_connection.execute("SELECT * FROM example").fetchall()


def test_saved_session_row_is_not_yet_canon(
    database: foundation_dependencies.repository.SqliteDatabase,
) -> None:
    """Verify a saved session row is not yet a canonical session."""
    sessions = foundation_dependencies.repository.SqliteSessionRepository(database)
    sessions.save(
        CODEX_HARNESS,
        foundation_test_events.example_session("candidate"),
    )
    assert not foundation_dependencies.repository.SqliteCanonicalEventRepository(database).session_ids()


def test_session_save_writes_identity_once(
    database: foundation_dependencies.repository.SqliteDatabase,
) -> None:
    """Verify session save writes identity once and live columns always.

    Identity columns are the first observation; the two live columns follow
        the session around — a resume lands in a new window with a new process.
    """
    sessions = foundation_dependencies.repository.SqliteSessionRepository(database)
    sessions.save(
        CODEX_HARNESS,
        foundation_dependencies.standard.replace(
            foundation_test_events.example_session(),
            terminal_window_id=foundation_dependencies.domain.domain_ids.WindowId("window-1"),
        ),
    )
    sessions.save(
        CODEX_HARNESS,
        foundation_dependencies.standard.replace(
            foundation_test_events.example_session(),
            working_directory="/elsewhere",
            terminal_window_id=foundation_dependencies.domain.domain_ids.WindowId("window-2"),
            harness_process_id=FIXTURE_PROCESS_ID,
        ),
    )
    loaded = sessions.find(PRIMARY_SESSION)
    assert loaded is not None
    assert loaded.working_directory == WORKING_DIRECTORY
    assert loaded.terminal_window_id == "window-2"
    assert loaded.harness_process_id == FIXTURE_PROCESS_ID
