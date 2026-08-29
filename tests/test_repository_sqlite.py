"""The storage layer on its own: every repository against a real database.

These tests build repositories directly, with no application graph and no
daemon, which is the whole point of the contract — a store that can only be
exercised through the thing that composes it is a store nobody tests.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier

import pytest

from audit.models import (
    ApplicationErrorRecord,
    SpawnRecord,
    StateFileRecord,
    StreamOpened,
)
from domain.entries import MessageBody, SessionEntry, ShellStartedBody
from domain.events import (
    CanonicalEvent,
    MessageCreated,
    SearchPerformed,
    SessionFinished,
    SessionStarted,
    ShellBackgrounded,
    ShellFinished,
    ShellOutputFinished,
    ShellStarted,
    TurnFinished,
)
from domain.ids import (
    ActorId,
    AttentionId,
    CanonicalEventId,
    HarnessName,
    MessageId,
    RequestId,
    ShellId,
    RawEventId,
    SessionId,
    TaskId,
    WindowId,
)
from domain.sessiondata import (
    ActorBackground,
    ActorFacts,
    ActorStatistics,
    ActorStatus,
    LifecycleState,
    SessionFacts,
    SessionGoal,
    ToolCount,
)
from domain.shells import ShellFollowState, ShellOutputFollowing
from domain.preferences import (
    NewSessionDraft,
    NewSessionPreferences,
    PushSigningKeypair,
    PushSubscription,
)
from domain.uploads import StoredUpload
from domain.values import (
    ActorRole,
    ExecutionMode,
    GoalState,
    MessagePhase,
    MessageRole,
    ModelReference,
    Outcome,
    ShellFollowUntil,
    StructuredContent,
    TextContent,
)
from domain.workspace import AnswerSelection, ComposerDraft, DialogDraft, QueuedMessage
from harness.models import RawEvent, Session, TranslationResult
from repository.errors import EventIdentityConflict, SchemaVersionMismatch
from repository.mapper import documents
from repository.mapper.documents import decode_document, encode_document
from repository.impl.sqlite.canonical_events import SqliteCanonicalEventRepository
from repository.impl.sqlite.connection import SqliteDatabase
from repository.impl.sqlite.databases import (
    audit_database,
    main_database,
    read_only,
)
from repository.impl.sqlite.audit import (
    SqliteAuditReadRepository,
    SqliteAuditWriteRepository,
)
from repository.impl.sqlite.raw_event_audits import SqliteRawEventAuditRepository
from repository.contract.session_data import SessionDataChanges
from repository.impl.sqlite.session_data import SqliteSessionDataRepository
from repository.impl.sqlite.shell_output import SqliteShellOutputRepository
from repository.impl.sqlite.preferences import (
    SqliteHiddenDirectoryRepository,
    SqliteNewSessionRepository,
    SqliteNotificationSettingRepository,
    SqlitePushSigningKeyRepository,
    SqlitePushSubscriptionRepository,
    SqliteTaskDismissalRepository,
    SqliteViewModeRepository,
)
from repository.impl.sqlite.raw_events import SqliteRawEventRepository
from repository.impl.sqlite.sessions import SqliteSessionRepository
from repository.impl.sqlite.schema import MAIN_SCHEMA_VERSION
from repository.impl.sqlite.terminal import (
    SqlitePaneWidthRepository,
)
from repository.impl.sqlite.uploads import SqliteUploadRepository
from repository.impl.sqlite.workspace import SqliteSessionWorkspaceRepository

SESSION = SessionId("session-one")
ACTOR = ActorId("actor-one")
HARNESS = HarnessName.CODEX


@pytest.fixture
def main(tmp_path):
    return main_database(str(tmp_path / "main.db"))


def a_session(
    terminal_window_id: WindowId | None = None,
    harness_process_id: int | None = None,
) -> Session:
    return Session(
        session_id=SESSION,
        lead_actor_id=ACTOR,
        source_reference="/transcripts/one.jsonl",
        working_directory="/project",
        terminal_window_id=terminal_window_id,
        harness_process_id=harness_process_id,
    )


def a_raw_event(identity: str = "raw-one", position: str = "1") -> RawEvent:
    return RawEvent(
        raw_event_id=RawEventId(identity),
        harness=HARNESS,
        source_type="hook",
        source_name="source",
        source_position=position,
        session_id=SESSION,
        actor_id=ACTOR,
        parent_actor_id=None,
        observed_at=1000.0,
        encoding="json",
        payload=b"{}",
        source_identity="example:hook",
    )


def a_started_event(event_id: str = "event-one") -> CanonicalEvent:
    return CanonicalEvent(
        event_id=CanonicalEventId(event_id),
        session_id=SESSION,
        actor_id=ACTOR,
        turn_id=None,
        parent_actor_id=None,
        harness=HARNESS,
        occurred_at=1000.0,
        terminal_window_id=None,
        harness_process_id=None,
        payload=SessionStarted("/project", "/transcripts/one.jsonl", None, None, None, None, None),
    )


# --- the file itself ----------------------------------------------------------


def test_the_schema_is_applied_once_and_the_version_is_recorded(main):
    main.initialize()
    main.initialize()
    with main.read() as connection:
        row = connection.execute("SELECT version FROM schema_version WHERE id=1").fetchone()
    assert row["version"] == main.schema_version


def test_a_file_written_by_another_schema_version_refuses_to_open(tmp_path):
    first = main_database(str(tmp_path / "main.db"))
    first.initialize()
    second = SqliteDatabase(first.path, first.schema, first.schema_version + 1)
    with pytest.raises(SchemaVersionMismatch):
        second.initialize()


def test_the_main_schema_is_created_whole_at_the_current_version(tmp_path):
    database = main_database(str(tmp_path / "main.db"))
    database.initialize()

    with database.read() as connection:
        version = connection.execute("SELECT version FROM schema_version WHERE id=1").fetchone()
        tables = {row["name"] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert version["version"] == MAIN_SCHEMA_VERSION
    assert {
        "raw_events",
        "pending_raw_events",
        "canonical_events",
        "interpretations",
        "shell_output",
    } <= tables


def _restore_version_six_queue_table(connection) -> None:
    connection.execute("DROP INDEX index_composer_queue_request")
    connection.execute("ALTER TABLE composer_queue_items RENAME TO composer_queue_items_v7")
    connection.execute(
        """
        CREATE TABLE composer_queue_items(
            session_id TEXT NOT NULL,
            position INTEGER NOT NULL,
            text TEXT NOT NULL,
            PRIMARY KEY(session_id, position),
            FOREIGN KEY(session_id) REFERENCES session_workspaces(session_id)
                ON DELETE CASCADE
        )
        """
    )
    connection.execute(
        "INSERT INTO composer_queue_items(session_id, position, text) "
        "SELECT session_id, position, text FROM composer_queue_items_v7"
    )
    connection.execute("DROP TABLE composer_queue_items_v7")


def _restore_version_eleven_schema(connection) -> None:
    connection.execute("DROP TRIGGER sessions_lifecycle_after_event")
    connection.execute("DROP TRIGGER sessions_lifecycle_after_insert")
    connection.execute("ALTER TABLE sessions DROP COLUMN lifecycle")
    connection.execute("ALTER TABLE sessions DROP COLUMN project_directory")


def _restore_version_ten_schema(connection) -> None:
    _restore_version_eleven_schema(connection)
    connection.execute("ALTER TABLE raw_events DROP COLUMN payload_codec")


def test_version_four_actor_models_are_migrated_to_the_domain_shape(tmp_path):
    path = str(tmp_path / "main.db")
    old_database = main_database(path)
    old_database.initialize()
    actor = replace(
        AN_ACTOR,
        model=ModelReference(name="claude-opus-5", display_name="opus-5"),
    )
    with old_database.write() as connection:
        connection.execute(
            """INSERT INTO session_data_actors(session_id, actor_id, revision, payload)
               VALUES (?, ?, ?, ?)""",
            (str(SESSION), str(actor.actor_id), 1, encode_document(actor).decode()),
        )
        connection.execute(
            """UPDATE session_data_actors
               SET payload = json_set(
                   json_remove(payload, '$.model.name'),
                   '$.model.native_id', json_extract(payload, '$.model.name'),
                   '$.model.selection_id', 'opus'
               )"""
        )
        _restore_version_six_queue_table(connection)
        _restore_version_ten_schema(connection)
        connection.execute("UPDATE schema_version SET version = 4 WHERE id = 1")

    upgraded = main_database(path)
    upgraded.initialize()

    with upgraded.read() as connection:
        row = connection.execute(
            "SELECT payload FROM session_data_actors WHERE actor_id = ?", (str(actor.actor_id),)
        ).fetchone()
        version = connection.execute("SELECT version FROM schema_version WHERE id = 1").fetchone()
    restored = decode_document(ActorFacts, row["payload"])
    assert restored.model == actor.model
    assert version["version"] == MAIN_SCHEMA_VERSION
    assert "native_id" not in row["payload"]
    assert "selection_id" not in row["payload"]


def test_version_five_closes_finished_codex_backgrounded_shells(tmp_path):
    path = str(tmp_path / "main.db")
    old_database = main_database(path)
    raw_events = SqliteRawEventRepository(old_database)
    canonical = SqliteCanonicalEventRepository(old_database)
    raw = a_raw_event()
    raw_events.record([raw])
    shell_id = ShellId("yielded-one")
    backgrounded = replace(
        a_started_event("backgrounded-one"),
        payload=ShellBackgrounded(shell_id),
    )
    finished = replace(
        a_started_event("finished-one"),
        payload=ShellFinished(shell_id, Outcome.SUCCEEDED, TextContent("done"), 0),
    )
    canonical.record_translation(
        raw,
        "1",
        TranslationResult((backgrounded, finished), "translated"),
        1001.0,
    )
    with old_database.write() as connection:
        _restore_version_six_queue_table(connection)
        _restore_version_ten_schema(connection)
        connection.execute("UPDATE schema_version SET version = 5 WHERE id = 1")

    upgraded = main_database(path)
    upgraded.initialize()

    repaired = SqliteCanonicalEventRepository(upgraded).find(
        CanonicalEventId("migration:6:shell-output-finished:finished-one")
    )
    assert repaired is not None
    assert repaired.payload == ShellOutputFinished(shell_id, Outcome.SUCCEEDED)
    with upgraded.read() as connection:
        version = connection.execute("SELECT version FROM schema_version WHERE id = 1").fetchone()
    assert version["version"] == MAIN_SCHEMA_VERSION


def test_version_six_queued_messages_gain_stable_request_identities(tmp_path):
    path = str(tmp_path / "main.db")
    old_database = main_database(path)
    old_database.initialize()
    with old_database.write() as connection:
        connection.execute(
            "INSERT INTO session_workspaces(session_id, queue_origin) VALUES(?, ?)",
            (str(SESSION), "browser"),
        )
        connection.executemany(
            "INSERT INTO composer_queue_items(session_id, position, request_id, text) VALUES(?, ?, ?, ?)",
            (
                (str(SESSION), 0, "request-one", "one"),
                (str(SESSION), 1, "request-two", "two"),
            ),
        )
        _restore_version_six_queue_table(connection)
        _restore_version_ten_schema(connection)
        connection.execute("UPDATE schema_version SET version = 6 WHERE id = 1")

    upgraded = main_database(path)
    upgraded.initialize()

    stored = SqliteSessionWorkspaceRepository(upgraded).find(SESSION)
    assert stored is not None and stored.queue is not None
    assert [item.request_id for item in stored.queue.items] == [
        "legacy:0",
        "legacy:1",
    ]
    with upgraded.read() as connection:
        version = connection.execute("SELECT version FROM schema_version WHERE id = 1").fetchone()
    assert version["version"] == MAIN_SCHEMA_VERSION


def test_version_seven_goals_gain_the_complete_state_shape(tmp_path):
    path = str(tmp_path / "main.db")
    old_database = main_database(path)
    old_database.initialize()
    facts = SessionFacts(
        session_id=SESSION,
        harness=HARNESS,
        state=LifecycleState.RUNNING,
        working_directory="/project",
        started_at=1000.0,
        lead_actor_id=ACTOR,
        goal=SessionGoal("Ship it", GoalState.COMPLETED, None),
    )
    with old_database.write() as connection:
        connection.execute(
            "INSERT INTO session_data(session_id, revision, payload) VALUES(?, ?, ?)",
            (str(SESSION), 1, encode_document(facts).decode()),
        )
        connection.execute(
            """
            UPDATE session_data
            SET payload = json_set(
                json_remove(payload, '$.goal.state', '$.goal.reason'),
                '$.goal.completed', true
            )
            """
        )
        _restore_version_ten_schema(connection)
        connection.execute("UPDATE schema_version SET version = 7 WHERE id = 1")

    upgraded = main_database(path)
    upgraded.initialize()

    with upgraded.read() as connection:
        row = connection.execute(
            "SELECT payload FROM session_data WHERE session_id = ?",
            (str(SESSION),),
        ).fetchone()
        version = connection.execute(
            "SELECT version FROM schema_version WHERE id = 1"
        ).fetchone()
    restored = decode_document(SessionFacts, row["payload"])
    assert restored.goal is not None
    assert restored.goal.state == GoalState.COMPLETED
    assert restored.goal.reason is None
    assert version["version"] == MAIN_SCHEMA_VERSION


def test_version_eight_settles_codex_shell_finishes_added_after_the_turn(tmp_path):
    path = str(tmp_path / "main.db")
    old_database = main_database(path)
    raw_events = SqliteRawEventRepository(old_database)
    canonical = SqliteCanonicalEventRepository(old_database)
    raw = a_raw_event()
    raw_events.record([raw])
    turn_finished = replace(
        a_started_event("turn-finished"),
        payload=TurnFinished(None, Outcome.SUCCEEDED),
    )
    shell_id = ShellId("late-parallel-command")
    shell_finished = replace(
        a_started_event("late-shell-finished"),
        payload=ShellFinished(shell_id, Outcome.SUCCEEDED, TextContent("done"), 0),
    )
    canonical.record_translation(
        raw,
        "1",
        TranslationResult((turn_finished, shell_finished), "translated"),
        1001.0,
    )
    with old_database.write() as connection:
        _restore_version_ten_schema(connection)
        connection.execute("UPDATE schema_version SET version = 8 WHERE id = 1")

    upgraded = main_database(path)
    upgraded.initialize()

    repaired = SqliteCanonicalEventRepository(upgraded).find(
        CanonicalEventId("migration:9:shell-settled:late-shell-finished")
    )
    assert repaired is not None
    assert repaired.payload == ShellOutputFinished(shell_id, Outcome.SUCCEEDED)
    with upgraded.read() as connection:
        version = connection.execute(
            "SELECT version FROM schema_version WHERE id = 1"
        ).fetchone()
    assert version["version"] == MAIN_SCHEMA_VERSION


@pytest.mark.parametrize(
    ("old_version", "backgrounded_after_replacement", "has_late_shell_finish"),
    (
        (14, False, False),
        (20, True, False),
        (21, True, True),
    ),
)
def test_schema_upgrade_closes_a_codex_shell_duplicated_after_restart(
    tmp_path,
    old_version,
    backgrounded_after_replacement,
    has_late_shell_finish,
):
    path = str(tmp_path / "main.db")
    old_database = main_database(path)
    raw_events = SqliteRawEventRepository(old_database)
    canonical = SqliteCanonicalEventRepository(old_database)
    raw = a_raw_event()
    raw_events.record([raw])
    original_shell = ShellId("call-before-restart")
    replacement_shell = ShellId("native-after-restart")
    command = TextContent("sleep 25")
    original_started = replace(
        a_started_event("original-started"),
        payload=ShellStarted(
            original_shell,
            command,
            ExecutionMode.FOREGROUND,
            None,
        ),
    )
    backgrounded = replace(
        a_started_event("original-backgrounded"),
        payload=ShellBackgrounded(original_shell),
    )
    replacement_started = replace(
        a_started_event("replacement-started"),
        payload=ShellStarted(
            replacement_shell,
            command,
            ExecutionMode.FOREGROUND,
            None,
        ),
    )
    replacement_finished = replace(
        a_started_event("replacement-finished"),
        payload=ShellFinished(
            replacement_shell,
            Outcome.SUCCEEDED,
            TextContent("done"),
            0,
        ),
    )
    original_finished = replace(
        a_started_event("original-finished"),
        payload=ShellFinished(
            original_shell,
            Outcome.CANCELLED,
            None,
            None,
        ),
    )
    replacement = (replacement_started, replacement_finished)
    ordered_events = (
        (original_started, *replacement, backgrounded)
        if backgrounded_after_replacement
        else (original_started, backgrounded, *replacement)
    )
    if has_late_shell_finish:
        ordered_events = (*ordered_events, original_finished)
    canonical.record_translation(
        raw,
        "7",
        TranslationResult(
            ordered_events,
            "translated",
        ),
        1001.0,
    )
    with old_database.write() as connection:
        actor = replace(
            AN_ACTOR,
            actor_id=ACTOR,
            background=ActorBackground(running_shell_ids=(original_shell,)),
            status=ActorStatus.AWAITING_BACKGROUND,
        )
        connection.execute(
            "INSERT INTO session_data_actors(session_id, actor_id, revision, payload) "
            "VALUES(?, ?, ?, ?)",
            (
                str(SESSION),
                str(ACTOR),
                1,
                encode_document(actor).decode(),
            ),
        )
        connection.execute(
            "UPDATE schema_version SET version = ? WHERE id = 1",
            (old_version,),
        )

    upgraded = main_database(path)
    upgraded.initialize()

    repaired = SqliteCanonicalEventRepository(upgraded).find(
        CanonicalEventId(
            "migration:15:recovered-shell-output-finished:original-backgrounded"
        )
    )
    assert repaired is not None
    assert repaired.payload == ShellOutputFinished(
        original_shell,
        Outcome.SUCCEEDED,
    )


def test_version_fifteen_finishes_a_resumed_run_with_a_deduplicated_exit(tmp_path):
    path = str(tmp_path / "main.db")
    old_database = main_database(path)
    sessions = SqliteSessionRepository(old_database)
    raw_events = SqliteRawEventRepository(old_database)
    canonical = SqliteCanonicalEventRepository(old_database)
    first_window = WindowId("window-one")
    resumed_window = WindowId("window-two")
    sessions.save(HARNESS, a_session(first_window, 101))

    first_started_raw = a_raw_event("first-started")
    first_exit = replace(
        a_raw_event("first-exit"),
        source_type="liveness",
        terminal_window_id=first_window,
    )
    resumed_started_raw = a_raw_event("resumed-started")
    resumed_exit = replace(
        a_raw_event("resumed-exit"),
        source_type="liveness",
        terminal_window_id=resumed_window,
    )
    raw_events.record(
        (first_started_raw, first_exit, resumed_started_raw, resumed_exit)
    )
    first_started = replace(
        a_started_event("first-run-started"),
        terminal_window_id=first_window,
        harness_process_id=101,
    )
    old_shared_finish = replace(
        a_started_event("old-shared-session-finish"),
        terminal_window_id=first_window,
        harness_process_id=101,
        payload=SessionFinished(Outcome.UNKNOWN, "process_exited"),
    )
    resumed_started = replace(
        a_started_event("resumed-run-started"),
        terminal_window_id=resumed_window,
        harness_process_id=202,
    )
    canonical.record_translation(
        first_started_raw,
        "1",
        TranslationResult((first_started,), "translated"),
        1001.0,
    )
    canonical.record_translation(
        first_exit,
        "1",
        TranslationResult((old_shared_finish,), "translated"),
        1002.0,
    )
    canonical.record_translation(
        resumed_started_raw,
        "1",
        TranslationResult((resumed_started,), "translated"),
        1003.0,
    )
    canonical.record_translation(
        resumed_exit,
        "1",
        TranslationResult((old_shared_finish,), "translated"),
        1004.0,
    )
    with old_database.write() as connection:
        connection.execute("UPDATE schema_version SET version = 15 WHERE id = 1")

    upgraded = main_database(path)
    upgraded.initialize()

    repaired = SqliteCanonicalEventRepository(upgraded).find(
        CanonicalEventId("migration:16:session-run-finished:resumed-exit")
    )
    assert repaired is not None
    assert repaired.terminal_window_id == resumed_window
    assert repaired.harness_process_id == 202
    assert repaired.payload == SessionFinished(Outcome.UNKNOWN, "process_exited")
    assert SqliteSessionRepository(upgraded).watchable() == ()


def test_version_sixteen_adds_the_covering_session_activity_index(tmp_path):
    path = str(tmp_path / "main.db")
    old_database = main_database(path)
    old_database.initialize()
    with old_database.write() as connection:
        connection.execute("DROP INDEX index_session_entries_session")
        connection.execute(
            "CREATE INDEX index_session_entries_session "
            "ON session_entries(session_id, cursor)"
        )
        connection.execute("UPDATE schema_version SET version = 16 WHERE id = 1")

    upgraded = main_database(path)
    upgraded.initialize()

    with upgraded.read() as connection:
        columns = tuple(
            row["name"]
            for row in connection.execute(
                "PRAGMA index_info(index_session_entries_session)"
            )
        )
        version = connection.execute(
            "SELECT version FROM schema_version WHERE id = 1"
        ).fetchone()
    assert columns == ("session_id", "cursor", "occurred_at")
    assert version["version"] == MAIN_SCHEMA_VERSION


def test_version_seventeen_requeues_ignored_claude_post_tool_hooks(tmp_path):
    path = str(tmp_path / "main.db")
    old_database = main_database(path)
    raw_events = SqliteRawEventRepository(old_database)
    canonical = SqliteCanonicalEventRepository(old_database)
    task_stop = replace(
        a_raw_event("task-stop"),
        harness=HarnessName.CLAUDE_CODE,
        source_name="PostToolUse",
        payload=(
            b'{"hook_event_name":"PostToolUse","tool_name":"TaskStop",'
            b'"tool_input":{"task_id":"background-one"}}'
        ),
    )
    unrelated = replace(
        a_raw_event("unrelated-hook"),
        harness=HarnessName.CODEX,
        source_name="PostToolUse",
    )
    raw_events.record([task_stop, unrelated])
    canonical.record_translation(
        task_stop,
        "3",
        TranslationResult((), "ignored_nonsemantic", "old TaskStop handling"),
        1001.0,
    )
    canonical.record_translation(
        unrelated,
        "3",
        TranslationResult((), "ignored_nonsemantic", "unrelated harness"),
        1001.0,
    )
    with old_database.write() as connection:
        connection.execute("UPDATE schema_version SET version = 17 WHERE id = 1")

    upgraded = main_database(path)
    upgraded.initialize()

    assert [event.raw_event_id for event in SqliteRawEventRepository(upgraded).unverdicted(10)] == [
        RawEventId("task-stop")
    ]
    with upgraded.read() as connection:
        remaining = connection.execute(
            "SELECT raw_event_id FROM interpretations ORDER BY raw_event_id"
        ).fetchall()
        version = connection.execute(
            "SELECT version FROM schema_version WHERE id = 1"
        ).fetchone()
    assert [row["raw_event_id"] for row in remaining] == ["unrelated-hook"]
    assert version["version"] == MAIN_SCHEMA_VERSION


def test_version_eighteen_reprocesses_structured_claude_search_results(tmp_path):
    path = str(tmp_path / "main.db")
    old_database = main_database(path)
    raw_events = SqliteRawEventRepository(old_database)
    canonical = SqliteCanonicalEventRepository(old_database)
    hook = replace(
        a_raw_event("tool-search-result"),
        harness=HarnessName.CLAUDE_CODE,
        source_name="PostToolUse",
        payload=(
            b'{"hook_event_name":"PostToolUse","tool_name":"ToolSearch",'
            b'"tool_input":{"query":"select:Monitor"},'
            b'"tool_response":{"matches":["Monitor"]}}'
        ),
    )
    raw_events.record([hook])
    old_event = CanonicalEvent(
        event_id=CanonicalEventId("old-tool-search"),
        session_id=SESSION,
        actor_id=ACTOR,
        turn_id=None,
        parent_actor_id=None,
        harness=HarnessName.CLAUDE_CODE,
        occurred_at=1000.0,
        terminal_window_id=None,
        harness_process_id=None,
        payload=SearchPerformed(
            "ToolSearch",
            TextContent("select:Monitor"),
            StructuredContent("{}"),
            Outcome.SUCCEEDED,
        ),
    )
    canonical.record_translation(
        hook,
        "3",
        TranslationResult((old_event,), "translated"),
        1001.0,
    )
    with old_database.write() as connection:
        connection.execute(
            "INSERT INTO session_entries("
            "cursor, entry_id, session_id, entry_type, actor_id, payload"
            ") VALUES(1, 'old-search-entry', ?, 'search', ?, '{}')",
            (str(SESSION), str(ACTOR)),
        )
        connection.execute(
            "INSERT INTO reaction_progress(id, canonical_cursor, updated_at) "
            "VALUES(1, 1, 1001.0)"
        )
        connection.execute("UPDATE schema_version SET version = 18 WHERE id = 1")

    upgraded = main_database(path)
    upgraded.initialize()

    assert [
        event.raw_event_id
        for event in SqliteRawEventRepository(upgraded).unverdicted(10)
    ] == [RawEventId("tool-search-result")]
    with upgraded.read() as connection:
        event_count = connection.execute(
            "SELECT COUNT(*) AS value FROM canonical_events "
            "WHERE event_id='old-tool-search'"
        ).fetchone()
        interpretation_count = connection.execute(
            "SELECT COUNT(*) AS value FROM interpretations "
            "WHERE raw_event_id='tool-search-result'"
        ).fetchone()
        entry_count = connection.execute(
            "SELECT COUNT(*) AS value FROM session_entries"
        ).fetchone()
        progress_count = connection.execute(
            "SELECT COUNT(*) AS value FROM reaction_progress"
        ).fetchone()
        version = connection.execute(
            "SELECT version FROM schema_version WHERE id = 1"
        ).fetchone()
    assert event_count["value"] == 0
    assert interpretation_count["value"] == 0
    assert entry_count["value"] == 0
    assert progress_count["value"] == 0
    assert version["version"] == MAIN_SCHEMA_VERSION


def test_version_nineteen_normalizes_canonical_model_references(tmp_path):
    path = str(tmp_path / "main.db")
    old_database = main_database(path)
    raw_events = SqliteRawEventRepository(old_database)
    canonical = SqliteCanonicalEventRepository(old_database)
    model_raw = a_raw_event("legacy-model", "1")
    context_raw = a_raw_event("legacy-context", "2")
    usage_raw = a_raw_event("legacy-usage", "3")
    raw_events.record([model_raw, context_raw, usage_raw])
    for raw_event, event_id in (
        (model_raw, "legacy-model-event"),
        (context_raw, "legacy-context-event"),
        (usage_raw, "legacy-usage-event"),
    ):
        canonical.record_translation(
            raw_event,
            "1",
            TranslationResult((a_started_event(event_id),), "translated"),
            1001.0,
        )
    legacy_model = (
        '{"native_id":"claude-fable-5","display_name":"fable-5",'
        '"selection_id":"fable"}'
    )
    with old_database.write() as connection:
        connection.execute(
            "UPDATE canonical_events SET event_type='model.changed', "
            "payload=json_object("
            "'previous', json(?), 'current', json(?), "
            "'reason', 'reported_by_harness') WHERE event_id='legacy-model-event'",
            (legacy_model, legacy_model),
        )
        connection.execute(
            "UPDATE canonical_events SET event_type='context.reported', "
            "payload=json_object('used_tokens', 1, 'window_tokens', 2, "
            "'model', json(?)) WHERE event_id='legacy-context-event'",
            (legacy_model,),
        )
        connection.execute(
            "UPDATE canonical_events SET event_type='usage.reported', "
            "payload=json_object('scope', 'session', 'subject_id', 'actor-one', "
            "'model', json(?), 'tokens', json_object(), 'cumulative', false) "
            "WHERE event_id='legacy-usage-event'",
            (legacy_model,),
        )
        connection.execute("UPDATE schema_version SET version = 19 WHERE id = 1")

    upgraded = main_database(path)
    upgraded.initialize()

    with upgraded.read() as connection:
        rows = connection.execute(
            "SELECT event_type, "
            "json_extract(payload, '$.current.name') AS current_name, "
            "json_extract(payload, '$.previous.name') AS previous_name, "
            "json_extract(payload, '$.model.name') AS model_name, "
            "json_extract(payload, '$.current.native_id') AS current_native, "
            "json_extract(payload, '$.model.native_id') AS model_native "
            "FROM canonical_events "
            "WHERE event_id LIKE 'legacy-%-event' ORDER BY event_type"
        ).fetchall()
        version = connection.execute(
            "SELECT version FROM schema_version WHERE id = 1"
        ).fetchone()
    assert len(rows) == 3
    for row in rows:
        name_field = "current_name" if row["event_type"] == "model.changed" else "model_name"
        native_field = (
            "current_native" if row["event_type"] == "model.changed" else "model_native"
        )
        assert row[name_field] == "claude-fable-5"
        assert row[native_field] is None
    model_row = next(row for row in rows if row["event_type"] == "model.changed")
    assert model_row["previous_name"] == "claude-fable-5"
    assert version["version"] == MAIN_SCHEMA_VERSION


def test_version_nine_builds_the_pending_raw_event_queue(tmp_path):
    path = str(tmp_path / "main.db")
    old_database = main_database(path)
    raw_events = SqliteRawEventRepository(old_database)
    canonical = SqliteCanonicalEventRepository(old_database)
    decided = a_raw_event("raw-decided", "1")
    pending = a_raw_event("raw-pending", "2")
    raw_events.record([decided, pending])
    canonical.record_translation(
        decided,
        "1",
        TranslationResult((), "ignored_unknown"),
        1001.0,
    )
    with old_database.write() as connection:
        connection.execute("DROP TABLE pending_raw_events")
        _restore_version_ten_schema(connection)
        connection.execute("UPDATE schema_version SET version = 9 WHERE id = 1")

    upgraded = main_database(path)
    upgraded.initialize()

    assert [event.raw_event_id for event in SqliteRawEventRepository(upgraded).unverdicted(10)] == [
        RawEventId("raw-pending")
    ]
    with upgraded.read() as connection:
        version = connection.execute(
            "SELECT version FROM schema_version WHERE id = 1"
        ).fetchone()
    assert version["version"] == MAIN_SCHEMA_VERSION


def test_version_eleven_builds_the_session_lifecycle_index(tmp_path):
    path = str(tmp_path / "main.db")
    old_database = main_database(path)
    sessions = SqliteSessionRepository(old_database)
    raw_events = SqliteRawEventRepository(old_database)
    canonical = SqliteCanonicalEventRepository(old_database)
    sessions.save(HARNESS, a_session())
    raw = a_raw_event()
    raw_events.record([raw])
    finished = replace(
        a_started_event("event-finished"),
        payload=SessionFinished(Outcome.SUCCEEDED, None),
    )
    canonical.record_translation(
        raw,
        "1",
        TranslationResult((finished,), "translated"),
        1001.0,
    )
    with old_database.write() as connection:
        _restore_version_eleven_schema(connection)
        connection.execute("UPDATE schema_version SET version = 11 WHERE id = 1")

    upgraded = main_database(path)
    upgraded.initialize()

    assert SqliteSessionRepository(upgraded).watchable() == ()
    with upgraded.read() as connection:
        session = connection.execute(
            "SELECT lifecycle FROM sessions WHERE session_id = ?",
            (str(SESSION),),
        ).fetchone()
        version = connection.execute(
            "SELECT version FROM schema_version WHERE id = 1"
        ).fetchone()
    assert session["lifecycle"] == "finished"
    assert version["version"] == MAIN_SCHEMA_VERSION


def test_version_twelve_adds_the_stable_project_identity(tmp_path):
    path = str(tmp_path / "main.db")
    old_database = main_database(path)
    sessions = SqliteSessionRepository(old_database)
    sessions.save(HARNESS, a_session())
    with old_database.write() as connection:
        connection.execute("ALTER TABLE sessions DROP COLUMN project_directory")
        connection.execute("UPDATE schema_version SET version = 12 WHERE id = 1")

    upgraded = main_database(path)
    upgraded.initialize()

    stored = SqliteSessionRepository(upgraded).find(SESSION)
    assert stored is not None
    assert stored.project_directory is None
    with upgraded.read() as connection:
        columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(sessions)")
        }
        version = connection.execute(
            "SELECT version FROM schema_version WHERE id = 1"
        ).fetchone()
    assert "project_directory" in columns
    assert version["version"] == MAIN_SCHEMA_VERSION


def test_a_read_only_database_never_creates_the_file(tmp_path):
    forensic = read_only(main_database(str(tmp_path / "absent.db")))
    forensic.initialize()
    assert not forensic.exists()


def test_a_failed_write_rolls_the_whole_transaction_back(main):
    sessions = SqliteSessionRepository(main)
    sessions.save(HARNESS, a_session())
    with pytest.raises(RuntimeError), main.write() as connection:
        connection.execute("DELETE FROM sessions")
        raise RuntimeError("boom")
    assert sessions.find(SESSION) is not None


def test_repository_transactions_reuse_one_connection_per_thread(main, monkeypatch):
    main.initialize()
    opened = []
    connect = main._connect

    def tracked_connect():
        connection = connect()
        opened.append(connection)
        return connection

    monkeypatch.setattr(main, "_connect", tracked_connect)

    with main.read() as connection_one:
        connection_one.execute("SELECT 1").fetchone()
    with main.read() as connection_two:
        connection_two.execute("SELECT 1").fetchone()
    with main.write() as connection_three:
        connection_three.execute("SELECT 1").fetchone()

    assert connection_one is connection_two is connection_three
    assert opened == [connection_one]


def test_nested_repository_transactions_fail_before_the_outer_transaction_changes(main):
    with main.read():
        with pytest.raises(RuntimeError, match="nested SQLite repository transaction"):
            with main.write():
                pass


def test_repository_connections_are_not_shared_between_threads(main):
    barrier = Barrier(2)

    def connection_identity() -> int:
        with main.read() as connection:
            barrier.wait()
            return id(connection)

    with ThreadPoolExecutor(max_workers=2) as executor:
        identities = tuple(executor.map(lambda _: connection_identity(), range(2)))

    assert identities[0] != identities[1]


# --- sessions -----------------------------------------------------------------


def test_a_session_upsert_writes_identity_once_and_refreshes_the_live_columns(main):
    sessions = SqliteSessionRepository(main)
    sessions.save(
        HARNESS,
        replace(a_session(), project_directory="/project-owner"),
    )
    sessions.save(
        HARNESS,
        replace(
            a_session(terminal_window_id=WindowId("7"), harness_process_id=42),
            project_directory="/different-owner",
        ),
    )
    stored = sessions.find(SESSION)
    assert stored is not None
    assert (stored.terminal_window_id, stored.harness_process_id) == ("7", 42)
    assert stored.project_directory == "/project-owner"

    reopened = SqliteSessionRepository(main_database(main.path)).find(SESSION)
    assert reopened is not None
    assert reopened.project_directory == "/project-owner"


def test_a_session_upsert_can_fill_a_missing_project_identity(main):
    sessions = SqliteSessionRepository(main)
    sessions.save(HARNESS, a_session())
    sessions.save(
        HARNESS,
        replace(a_session(), project_directory="/project-owner"),
    )

    stored = sessions.find(SESSION)

    assert stored is not None
    assert stored.project_directory == "/project-owner"


def test_a_finished_session_leaves_the_watchable_set(main):
    sessions = SqliteSessionRepository(main)
    canonical = SqliteCanonicalEventRepository(main)
    raw_events = SqliteRawEventRepository(main)
    sessions.save(HARNESS, a_session())
    assert [session.session_id for session in sessions.watchable()] == [SESSION]

    raw_events.record([a_raw_event()])
    finished = CanonicalEvent(
        event_id=CanonicalEventId("event-finished"),
        session_id=SESSION,
        actor_id=ACTOR,
        turn_id=None,
        parent_actor_id=None,
        harness=HARNESS,
        occurred_at=1001.0,
        terminal_window_id=None,
        harness_process_id=None,
        payload=SessionFinished(Outcome.SUCCEEDED, None),
    )
    canonical.record_translation(a_raw_event(), "1", TranslationResult((finished,), "translated"), 1001.0)
    assert sessions.watchable() == ()

    resumed_raw = a_raw_event("raw-resumed", "2")
    raw_events.record([resumed_raw])
    resumed = a_started_event("event-resumed")
    canonical.record_translation(
        resumed_raw,
        "1",
        TranslationResult((resumed,), "translated"),
        1002.0,
    )
    assert [session.session_id for session in sessions.watchable()] == [SESSION]

    refinished_raw = a_raw_event("raw-refinished", "3")
    raw_events.record([refinished_raw])
    refinished = replace(finished, event_id=CanonicalEventId("event-refinished"))
    canonical.record_translation(
        refinished_raw,
        "1",
        TranslationResult((refinished,), "translated"),
        1003.0,
    )
    assert sessions.watchable() == ()


# --- raw observations ---------------------------------------------------------


def test_re_recording_an_identical_observation_is_a_no_op(main):
    raw_events = SqliteRawEventRepository(main)
    raw_events.record([a_raw_event()])
    raw_events.record([a_raw_event()])
    assert raw_events.find(RawEventId("raw-one")) is not None


def test_reusing_an_identity_for_different_bytes_is_corruption(main):
    raw_events = SqliteRawEventRepository(main)
    raw_events.record([a_raw_event()])
    with pytest.raises(EventIdentityConflict):
        raw_events.record([a_raw_event(position="2")])


def test_resume_positions_come_back_for_every_source_in_one_call(main):
    raw_events = SqliteRawEventRepository(main)
    raw_events.record([a_raw_event("raw-one", "1")])
    second = RawEvent(
        raw_event_id=RawEventId("raw-two"),
        harness=HARNESS,
        source_type="hook",
        source_name="source",
        source_position="9",
        session_id=SESSION,
        actor_id=ACTOR,
        parent_actor_id=None,
        observed_at=1002.0,
        encoding="json",
        payload=b"{}",
        source_identity="example:other",
    )
    raw_events.record([second])
    positions = raw_events.latest_positions(["example:hook", "example:other", "example:absent"])
    assert positions == {"example:hook": "1", "example:other": "9"}


def test_the_backlog_is_evidence_without_a_verdict_in_arrival_order(main):
    raw_events = SqliteRawEventRepository(main)
    canonical = SqliteCanonicalEventRepository(main)
    raw_events.record([a_raw_event()])
    assert [event.raw_event_id for event in raw_events.unverdicted(10)] == [RawEventId("raw-one")]
    canonical.record_translation(a_raw_event(), "1", TranslationResult((), "ignored_unknown"), 1000.0)
    assert raw_events.unverdicted(10) == ()


def test_raw_evidence_is_compressed_in_storage_and_restored_exactly(main):
    raw_events = SqliteRawEventRepository(main)
    raw = replace(a_raw_event(), payload=b'{"text":"' + b"repeat " * 1_000 + b'"}')

    raw_events.record([raw])

    with main.read() as connection:
        stored = connection.execute(
            "SELECT payload, payload_codec FROM raw_events WHERE raw_event_id=?",
            (str(raw.raw_event_id),),
        ).fetchone()
    assert stored["payload_codec"] == "zlib"
    assert len(stored["payload"]) < len(raw.payload)
    assert raw_events.find(raw.raw_event_id) == raw
    raw_events.record([raw])


# --- canonical facts ----------------------------------------------------------


def test_one_translation_writes_verdict_facts_and_provenance_together(main):
    raw_events = SqliteRawEventRepository(main)
    canonical = SqliteCanonicalEventRepository(main)
    raw_events.record([a_raw_event()])
    outcome = canonical.record_translation(
        a_raw_event(), "1", TranslationResult((a_started_event(),), "translated"), 1000.0
    )
    assert [event.event_id for event in outcome.accepted] == [CanonicalEventId("event-one")]
    stored = canonical.find(CanonicalEventId("event-one"))
    assert stored is not None
    assert stored.raw_event_ids == (RawEventId("raw-one"),)


def test_a_re_observed_fact_adds_provenance_and_is_not_re_accepted(main):
    raw_events = SqliteRawEventRepository(main)
    canonical = SqliteCanonicalEventRepository(main)
    second = a_raw_event("raw-two", "2")
    raw_events.record([a_raw_event(), second])
    canonical.record_translation(a_raw_event(), "1", TranslationResult((a_started_event(),), "translated"), 1000.0)
    outcome = canonical.record_translation(second, "1", TranslationResult((a_started_event(),), "translated"), 1001.0)
    assert outcome.accepted == ()
    assert [event.event_id for event in outcome.deduplicated] == [CanonicalEventId("event-one")]
    stored = canonical.find(CanonicalEventId("event-one"))
    assert stored is not None
    assert set(stored.raw_event_ids) == {RawEventId("raw-one"), RawEventId("raw-two")}


def test_the_reaction_loops_page_walks_every_session_in_commit_order(main):
    """The ONE read over the fact log now, and the reason it is not per session.

    Reactions happen in the order the world saw them, not per session, so the
    loop reads across all of them from a single cursor and resumes from the last
    one it saw. The five per-session paging methods this replaced existed for the
    read-time folds, and there are none: what a session IS lives in the read
    model, written once as the facts arrived.
    """
    raw_events = SqliteRawEventRepository(main)
    canonical = SqliteCanonicalEventRepository(main)
    other = SessionId("session-two")
    for index, session_id in enumerate((SESSION, other, SESSION)):
        raw = a_raw_event(f"raw-{index}", str(index))
        raw_events.record([raw])
        canonical.record_translation(
            raw,
            "1",
            TranslationResult(
                (
                    CanonicalEvent(
                        event_id=CanonicalEventId(f"event-{index}"),
                        session_id=session_id,
                        actor_id=ACTOR,
                        turn_id=None,
                        parent_actor_id=None,
                        harness=HARNESS,
                        occurred_at=1000.0 + index,
                        terminal_window_id=None,
                        harness_process_id=None,
                        payload=MessageCreated(
                            MessageId(f"m{index}"), MessageRole.USER, TextContent("hi"), MessagePhase.PROMPT, None
                        ),
                    ),
                ),
                "translated",
            ),
            1000.0 + index,
        )

    whole = canonical.page_from(0, 10)
    assert [committed.event_id for committed in whole] == [
        CanonicalEventId("event-0"),
        CanonicalEventId("event-1"),
        CanonicalEventId("event-2"),
    ]
    assert [committed.session_id for committed in whole] == [SESSION, other, SESSION]
    # Cursors ascend, and resuming from one returns exactly what follows it.
    cursors = [committed.cursor for committed in whole]
    assert cursors == sorted(cursors)
    assert [committed.event_id for committed in canonical.page_from(cursors[0], 10)] == [
        CanonicalEventId("event-1"),
        CanonicalEventId("event-2"),
    ]
    # The limit is the batch boundary, and it never skips: a smaller page is the
    # same walk in more steps.
    assert [committed.cursor for committed in canonical.page_from(0, 2)] == cursors[:2]


# --- raw-event audit ----------------------------------------------------------


def test_raw_event_audit_joins_an_observation_to_its_interpretation_and_facts(main):
    raw_events = SqliteRawEventRepository(main)
    canonical = SqliteCanonicalEventRepository(main)
    audits = SqliteRawEventAuditRepository(main)
    raw_events.record([a_raw_event()])
    canonical.record_translation(a_raw_event(), "3", TranslationResult((a_started_event(),), "translated"), 1000.0)
    one = audits.audit(RawEventId("raw-one"))
    assert one is not None
    assert one.interpretation is not None
    assert one.interpretation.decision == "translated"
    assert one.interpretation.translator_version == "3"
    assert [item.event.event_id for item in one.interpretation.events] == [CanonicalEventId("event-one")]
    assert audits.audits_for_session(SESSION) == (one,)


def test_uninterpreted_raw_event_audit_has_no_interpretation(main):
    raw_events = SqliteRawEventRepository(main)
    audits = SqliteRawEventAuditRepository(main)
    raw_events.record([a_raw_event()])
    one = audits.audit(RawEventId("raw-one"))
    assert one is not None and one.interpretation is None


# --- shell output -------------------------------------------------------------


def a_following(
    until: ShellFollowUntil = ShellFollowUntil.SHELL_FINISHED,
) -> ShellOutputFollowing:
    return ShellOutputFollowing(
        session_id=SESSION,
        shell_id=ShellId("op-one"),
        harness=HARNESS,
        actor_id=ACTOR,
        parent_actor_id=None,
        source_path="/tmp/output",
        chunk_source_type="chunk",
        delete_source=True,
        initial_size=0,
        initial_modified_at=0,
        wait_for_source_change=False,
        until=until,
        state=ShellFollowState.ACTIVE,
        created_at=1000.0,
    )


def test_a_following_round_trips_without_a_driver_row(main):
    outputs = SqliteShellOutputRepository(main)
    outputs.save(a_following())
    assert outputs.find_for_session(SESSION) == (a_following(),)


def test_one_shell_can_follow_several_output_files(main):
    outputs = SqliteShellOutputRepository(main)
    first = a_following()
    second = replace(first, source_path="/tmp/second-output")

    outputs.save(first)
    outputs.save(second)

    assert outputs.find_for_session(SESSION) == (first, second)
    outputs.mark_finishing(SESSION, first.shell_id)
    assert all(item.finishing for item in outputs.find_for_session(SESSION))
    outputs.remove(SESSION, first.shell_id, first.source_path)
    assert outputs.find_for_session(SESSION) == (
        replace(second, state=ShellFollowState.FINISHING),
    )


def test_version_twenty_two_following_moves_to_the_several_file_key(tmp_path):
    path = str(tmp_path / "main.db")
    old_database = main_database(path)
    old_outputs = SqliteShellOutputRepository(old_database)
    old_outputs.save(a_following())
    with old_database.write() as connection:
        connection.execute("ALTER TABLE shell_output RENAME TO shell_output_new_key")
        connection.execute(
            """
            CREATE TABLE shell_output(
                session_id TEXT NOT NULL,
                shell_id TEXT NOT NULL,
                harness TEXT NOT NULL,
                actor_id TEXT NOT NULL,
                parent_actor_id TEXT,
                source_path TEXT NOT NULL,
                chunk_source_type TEXT NOT NULL,
                delete_source INTEGER NOT NULL,
                initial_size INTEGER NOT NULL,
                initial_modified_at INTEGER NOT NULL,
                wait_for_source_change INTEGER NOT NULL,
                until TEXT NOT NULL CHECK(until IN ('shell_finished', 'session_finished')),
                state TEXT NOT NULL CHECK(state IN ('active', 'finishing')),
                created_at REAL NOT NULL,
                PRIMARY KEY(session_id, shell_id)
            )
            """
        )
        connection.execute("INSERT INTO shell_output SELECT * FROM shell_output_new_key")
        connection.execute("DROP TABLE shell_output_new_key")
        connection.execute("UPDATE schema_version SET version=22 WHERE id=1")

    upgraded = main_database(path)
    upgraded.initialize()
    outputs = SqliteShellOutputRepository(upgraded)
    second = replace(a_following(), source_path="/tmp/second-output")
    outputs.save(second)

    assert outputs.find_for_session(SESSION) == (a_following(), second)


def test_marking_finished_ends_only_a_foreground_following(main):
    outputs = SqliteShellOutputRepository(main)
    outputs.save(a_following(until=ShellFollowUntil.SESSION_FINISHED))
    outputs.mark_shell_finished(SESSION, ShellId("op-one"))
    assert outputs.find_for_session(SESSION)[0].state == ShellFollowState.ACTIVE
    outputs.mark_finishing(SESSION, ShellId("op-one"))
    assert outputs.find_for_session(SESSION)[0].finishing


def test_expiry_returns_what_it_removed_so_the_caller_unlinks(main):
    outputs = SqliteShellOutputRepository(main)
    outputs.save(a_following())
    removed = outputs.remove_expired(2000.0)
    assert [following.source_path for following in removed] == ["/tmp/output"]
    assert outputs.find_for_session(SESSION) == ()


# --- the read model -----------------------------------------------------------


# One of each, and `replace` for the differences. A dict of defaults updated
# with kwargs is the same builder untyped: every field arrives as `object`, so a
# test could pass a string where a Literal belongs and nothing would say so.
A_SESSION = SessionFacts(
    session_id=SESSION,
    harness=HARNESS,
    state=LifecycleState.RUNNING,
    working_directory="/work",
    started_at=1.0,
    lead_actor_id=ActorId("lead"),
)
AN_ACTOR = ActorFacts(
    session_id=SESSION,
    actor_id=ActorId("lead"),
    role=ActorRole.LEAD,
    name="claude",
    state=LifecycleState.RUNNING,
)


def test_version_twenty_four_names_stored_tool_count_fields(tmp_path):
    path = str(tmp_path / "main.db")
    old_database = main_database(path)
    old_store = SqliteSessionDataRepository(old_database)
    old_store.apply(
        SESSION,
        SessionDataChanges(session=A_SESSION, actors=(AN_ACTOR,)),
        1,
    )
    with old_database.write() as connection:
        connection.execute(
            "UPDATE session_data_actors SET payload = json_set("
            "payload, '$.statistics.tool_counts', json(?))",
            ('[["Bash", 2], ["Read", 1]]',),
        )
        connection.execute("UPDATE schema_version SET version=23 WHERE id=1")

    upgraded = main_database(path)
    upgraded.initialize()
    session_data = SqliteSessionDataRepository(upgraded).read(SESSION)

    assert session_data is not None
    assert session_data.actors[0].statistics == replace(
        ActorStatistics(),
        tool_counts=(ToolCount("Bash", 2), ToolCount("Read", 1)),
    )


def test_document_mapper_reuses_one_adapter_for_each_shape():
    documents._adapter.cache_clear()
    try:
        payload = encode_document(AN_ACTOR)
        first = decode_document(ActorFacts, payload)
        second = decode_document(ActorFacts, payload)
        cache_info = documents._adapter.cache_info()
    finally:
        documents._adapter.cache_clear()

    assert first == AN_ACTOR
    assert second == AN_ACTOR
    assert cache_info.misses == 1
    assert cache_info.hits == 2


def test_lead_session_read_omits_child_actor_rows(main):
    store = SqliteSessionDataRepository(main)
    child = replace(
        AN_ACTOR,
        actor_id=ActorId("child-one"),
        role=ActorRole.CHILD,
        parent_actor_id=AN_ACTOR.actor_id,
    )
    store.apply(
        SESSION,
        SessionDataChanges(session=A_SESSION, actors=(AN_ACTOR, child)),
        1,
    )

    leads = store.lead_sessions()

    assert len(leads) == 1
    assert leads[0].session == A_SESSION
    assert leads[0].lead == AN_ACTOR


def test_working_directories_are_unique_and_most_recent_first(main):
    store = SqliteSessionDataRepository(main)
    other_session = SessionId("other-session")
    store.apply(
        SESSION,
        SessionDataChanges(session=replace(A_SESSION, started_at=1.0)),
        1,
    )
    store.apply(
        other_session,
        SessionDataChanges(
            session=replace(
                A_SESSION,
                session_id=other_session,
                working_directory="/other",
                started_at=3.0,
                state=LifecycleState.FINISHED,
                finished_at=3.5,
            )
        ),
        2,
    )
    newest_session = SessionId("newest-session")
    store.apply(
        newest_session,
        SessionDataChanges(
            session=replace(
                A_SESSION,
                session_id=newest_session,
                started_at=4.0,
            )
        ),
        3,
    )

    assert store.working_directories() == ("/work", "/other")


def an_entry(entry_id: str) -> SessionEntry:
    return SessionEntry(
        entry_id=CanonicalEventId(entry_id),
        session_id=SESSION,
        actor_id=ActorId("lead"),
        parent_actor_id=None,
        turn_id=None,
        occurred_at=1.0,
        summary=None,
        body=MessageBody(MessageId(entry_id), MessageRole.USER, MessagePhase.PROMPT, TextContent("go")),
    )


def test_the_canonical_cursor_stamps_entries_and_aggregate_revisions_alike(main):
    """The whole stream mechanism: an entry's cursor and an aggregate row's
    revision use the SAME canonical cursor, so "everything after C" is one
    question with one answer across both kinds of change."""
    store = SqliteSessionDataRepository(main)

    first = store.apply(SESSION, SessionDataChanges(session=A_SESSION, actors=(AN_ACTOR,)), 10)
    second = store.apply(SESSION, SessionDataChanges(entry=an_entry("e1")), 11)
    third = store.apply(SESSION, SessionDataChanges(actors=(replace(AN_ACTOR, status=ActorStatus.WORKING),)), 12)

    assert (first, second, third) == (10, 11, 12)
    data = store.read(SESSION)
    assert data.cursor == 12
    assert store.entries_page(SESSION, limit=10).items[0].cursor == 11
    # …and the mark moved with the rows, every time.
    assert store.progress() == 12


def test_an_aggregate_read_reports_the_high_water_mark_across_both_kinds(main):
    """A stream must not start from the aggregate's own revision: it routinely
    lags the newest entry, and starting there re-sends what the client has."""
    store = SqliteSessionDataRepository(main)
    store.apply(SESSION, SessionDataChanges(session=A_SESSION), 1)
    store.apply(SESSION, SessionDataChanges(entry=an_entry("e1")), 2)

    data = store.read(SESSION)
    assert data.session.state == "running"
    assert data.cursor == 2


def test_a_stale_process_cannot_hand_out_a_cursor_a_client_already_holds(main):
    """A rebuild in another process must not make a live stream move back.

    This is the production failure from session 01a03de0: the daemon cached its
    next revision while a rebuild process was still filling the projection.
    After the rebuild reached a higher cursor, the daemon wrote the next prompt
    below the browser's boundary, so the prompt was never reconciled.
    """
    first = SqliteSessionDataRepository(main)
    first.apply(SESSION, SessionDataChanges(entry=an_entry("e1")), 1)
    rebuilding = SqliteSessionDataRepository(main)
    rebuilding.apply(SESSION, SessionDataChanges(entry=an_entry("e100")), 100)

    assert first.apply(SESSION, SessionDataChanges(entry=an_entry("e101")), 101) == 101
    assert [entry.cursor for entry in first.entries_page(SESSION, limit=10).items] == [1, 100, 101]


def test_an_entry_is_written_once_however_often_its_event_is_replayed(main):
    store = SqliteSessionDataRepository(main)
    store.apply(SESSION, SessionDataChanges(entry=an_entry("e1")), 1)
    store.apply(SESSION, SessionDataChanges(entry=an_entry("e1")), 1)
    assert len(store.entries_page(SESSION, limit=10).items) == 1


def test_a_page_is_read_as_of_a_cursor_so_it_agrees_with_the_snapshot(main):
    store = SqliteSessionDataRepository(main)
    for ordinal in range(1, 6):
        store.apply(SESSION, SessionDataChanges(entry=an_entry("e%d" % ordinal)), ordinal)

    whole = store.entries_page(SESSION, limit=10)
    assert [entry.entry_id for entry in whole.items] == ["e1", "e2", "e3", "e4", "e5"]
    assert whole.has_more is False

    snapshot = store.entries_page(SESSION, at=3, limit=10)
    assert [entry.entry_id for entry in snapshot.items] == ["e1", "e2", "e3"]

    newest = store.entries_page(SESSION, limit=2)
    assert [entry.entry_id for entry in newest.items] == ["e4", "e5"]
    assert (newest.oldest_cursor, newest.has_more) == (4, True)
    older = store.entries_page(SESSION, before=newest.oldest_cursor, limit=2)
    assert [entry.entry_id for entry in older.items] == ["e2", "e3"]


def test_the_deltas_answer_only_what_changed_after_a_cursor(main):
    store = SqliteSessionDataRepository(main)
    store.apply(SESSION, SessionDataChanges(session=A_SESSION, actors=(AN_ACTOR,)), 1)
    boundary = store.read(SESSION).cursor
    store.apply(SESSION, SessionDataChanges(entry=an_entry("e1")), 2)
    store.apply(SESSION, SessionDataChanges(actors=(replace(AN_ACTOR, status=ActorStatus.WORKING),)), 3)

    delta = store.delta(SESSION, boundary)
    assert [entry.entry_id for entry in delta.entries] == ["e1"]
    assert delta.session is None
    assert [actor.status for actor in delta.actors] == ["working"]
    # The cursor it reached, which is what a stream sends back: without it an
    # aggregate-only change would be re-sent on every poll forever.
    assert delta.cursor == 3
    assert store.delta(SESSION, delta.cursor).empty

    across = store.changed_after(boundary)
    assert across.sessions == ()
    assert [actor.actor_id for actor in across.actors] == [ActorId("lead")]
    assert across.cursor == 3
    assert store.changed_after(0).sessions[0].session_id == SESSION


def test_an_entry_body_decodes_as_the_shape_its_own_type_names(main):
    """The payload column is a closed typed document, not a blob: what comes back
    is the body class the `entry_type` names, validated."""
    store = SqliteSessionDataRepository(main)
    store.apply(
        SESSION,
        SessionDataChanges(
            entry=replace(
                an_entry("e1"),
                body=ShellStartedBody(ShellId("sh1"), TextContent("make test"), ExecutionMode.BACKGROUND),
            )
        ),
        1,
    )
    stored = store.entries_page(SESSION, limit=10).items[0]
    assert stored.entry_type == "shell_started"
    assert stored.body == ShellStartedBody(ShellId("sh1"), TextContent("make test"), ExecutionMode.BACKGROUND)


def test_clearing_the_read_model_keeps_replayed_canonical_cursor_identity(main):
    """A rebuild gives a fact the same cursor it had before the clear."""
    store = SqliteSessionDataRepository(main)
    store.apply(SESSION, SessionDataChanges(session=A_SESSION, entry=an_entry("e1")), 7)
    store.clear()

    assert store.read(SESSION) is None
    assert store.visible() == ()
    assert store.progress() == 0
    assert store.apply(SESSION, SessionDataChanges(entry=an_entry("e1")), 7) == 7
    assert store.entries_page(SESSION, limit=10).items[0].cursor == 7


def test_the_list_view_reads_every_session_with_its_own_cursor(main):
    store = SqliteSessionDataRepository(main)
    other = SessionId("session-two")
    store.apply(SESSION, SessionDataChanges(session=A_SESSION, actors=(AN_ACTOR,)), 1)
    store.apply(
        other,
        SessionDataChanges(
            session=replace(A_SESSION, session_id=other, title="Other"),
            actors=(replace(AN_ACTOR, session_id=other),),
        ),
        2,
    )
    store.apply(SESSION, SessionDataChanges(entry=an_entry("e1")), 3)

    listed = {data.session.session_id: data for data in store.visible()}
    assert set(listed) == {SESSION, other}
    assert listed[SESSION].cursor == 3
    assert listed[other].session.title == "Other"
    assert [actor.actor_id for actor in listed[other].actors] == [ActorId("lead")]


def test_the_running_list_does_not_read_finished_session_aggregates(main):
    store = SqliteSessionDataRepository(main)
    finished_id = SessionId("session-finished")
    store.apply(
        SESSION,
        SessionDataChanges(session=A_SESSION, actors=(AN_ACTOR,)),
        1,
    )
    store.apply(
        finished_id,
        SessionDataChanges(
            session=replace(
                A_SESSION,
                session_id=finished_id,
                state=LifecycleState.FINISHED,
                finished_at=2.0,
            ),
            actors=(
                replace(
                    AN_ACTOR,
                    session_id=finished_id,
                    state=LifecycleState.FINISHED,
                ),
            ),
        ),
        2,
    )
    store.apply(SESSION, SessionDataChanges(entry=an_entry("e1")), 3)

    running = store.running()

    assert [data.session.session_id for data in running] == [SESSION]
    assert running[0].actors == (AN_ACTOR,)
    assert running[0].cursor == 3


# --- the session workspace ----------------------------------------------------


def test_an_older_composer_draft_never_clobbers_a_newer_one(main):
    workspace = SqliteSessionWorkspaceRepository(main)
    assert workspace.save_composer_draft(SESSION, ComposerDraft("second", "web", 2.0))
    assert not workspace.save_composer_draft(SESSION, ComposerDraft("first", "web", 1.0))
    stored = workspace.find(SESSION)
    assert stored is not None and stored.draft is not None
    assert stored.draft.text == "second"


def test_the_queue_and_the_dialog_round_trip_as_rows(main):
    workspace = SqliteSessionWorkspaceRepository(main)
    workspace.enqueue_composer_message(SESSION, QueuedMessage(RequestId("request-one"), "one"), "send")
    workspace.enqueue_composer_message(SESSION, QueuedMessage(RequestId("request-two"), "two"), "send")
    workspace.save_dialog_draft(
        SESSION,
        DialogDraft(
            AttentionId("attention-one"),
            (AnswerSelection(("a", "b"), "other text"),),
            "web",
        ),
    )
    stored = workspace.find(SESSION)
    assert stored is not None
    assert stored.queue is not None
    assert [message.text for message in stored.queue.items] == ["one", "two"]
    assert [message.request_id for message in stored.queue.items] == [
        "request-one",
        "request-two",
    ]
    assert stored.dialog is not None
    assert stored.dialog.answers[0].selected == ("a", "b")
    assert stored.dialog.answers[0].other == "other text"


def test_a_queued_request_is_idempotent_and_can_be_removed_by_identity(main):
    workspace = SqliteSessionWorkspaceRepository(main)
    message = QueuedMessage(RequestId("request-one"), "one")
    workspace.enqueue_composer_message(SESSION, message, "send")
    workspace.enqueue_composer_message(SESSION, message, "send")
    stored = workspace.find(SESSION)
    assert stored is not None and stored.queue is not None
    assert stored.queue.items == (message,)

    workspace.remove_queued_message(SESSION, RequestId("request-one"))
    stored = workspace.find(SESSION)
    assert stored is not None and stored.queue is None


# --- preferences --------------------------------------------------------------


def test_a_view_mode_is_stored_and_cleared(main):
    view_modes = SqliteViewModeRepository(main)
    assert view_modes.view_mode(SESSION) is None
    view_modes.set_view_mode(SESSION, "focus")
    assert view_modes.view_mode(SESSION) == "focus"
    view_modes.clear_view_mode(SESSION)
    assert view_modes.view_mode(SESSION) is None


def test_alerting_defaults_on_and_mutes_come_back_in_one_call(main):
    notifications = SqliteNotificationSettingRepository(main)
    assert notifications.alerting_enabled()
    notifications.set_alerting_enabled(False)
    assert not notifications.alerting_enabled()
    notifications.set_muted(SESSION, True)
    assert notifications.muted_session_ids() == frozenset({SESSION})
    notifications.set_muted(SESSION, False)
    assert notifications.muted_session_ids() == frozenset()


def test_hiding_a_directory_twice_keeps_the_newer_stamp(main):
    directories = SqliteHiddenDirectoryRepository(main)
    directories.hide("/project", 1.0)
    directories.hide("/project", 2.0)
    assert [(entry.working_directory, entry.hidden_at) for entry in directories.hidden()] == [("/project", 2.0)]


def test_a_stale_new_session_draft_is_rejected_and_the_map_is_pruned(main):
    new_sessions = SqliteNewSessionRepository(main)
    assert not new_sessions.save_draft(NewSessionDraft("/a", "newer", 2.0), 10).stale
    assert new_sessions.save_draft(NewSessionDraft("/a", "older", 1.0), 10).stale
    assert [draft.text for draft in new_sessions.drafts()] == ["newer"]

    for index in range(5):
        new_sessions.save_draft(NewSessionDraft(f"/dir{index}", "x", float(index + 10)), 2)
    assert len(new_sessions.drafts()) == 2


def test_new_session_preferences_round_trip(main):
    new_sessions = SqliteNewSessionRepository(main)
    assert new_sessions.preferences() is None
    new_sessions.save_preferences(NewSessionPreferences("/project", HARNESS, "opus", "high"))
    assert new_sessions.preferences() == NewSessionPreferences("/project", HARNESS, "opus", "high")


def test_task_dismissals_store_the_id_set_and_prune_by_session(main):
    dismissals = SqliteTaskDismissalRepository(main)
    dismissals.dismiss(SESSION, [TaskId("t1"), TaskId("t2")], 1.0, 10)
    assert dismissals.dismissed_task_ids(SESSION) == frozenset({TaskId("t1"), TaskId("t2")})
    dismissals.restore(SESSION)
    assert dismissals.dismissed_task_ids(SESSION) == frozenset()

    for index in range(4):
        dismissals.dismiss(SessionId(f"s{index}"), [TaskId("t")], float(index), 2)
    remaining = [
        session for session in (SessionId(f"s{index}") for index in range(4)) if dismissals.dismissed_task_ids(session)
    ]
    assert len(remaining) == 2


def test_push_subscriptions_upsert_by_endpoint(main):
    subscriptions = SqlitePushSubscriptionRepository(main)
    subscriptions.upsert(PushSubscription("https://push/1", "p", "a", "device", None, 1.0))
    subscriptions.upsert(PushSubscription("https://push/1", "p2", "a2", "device", "phone", 2.0))
    assert len(subscriptions.subscriptions()) == 1
    assert subscriptions.subscriptions()[0].device_label == "phone"
    subscriptions.remove("https://push/1")
    assert subscriptions.subscriptions() == ()


def test_the_push_signing_keypair_is_a_singleton(main):
    keys = SqlitePushSigningKeyRepository(main)
    assert keys.keypair() is None
    keys.save_keypair(PushSigningKeypair("pem", "pub"))
    keys.save_keypair(PushSigningKeypair("pem2", "pub2"))
    assert keys.keypair() == PushSigningKeypair("pem2", "pub2")


# --- terminal -----------------------------------------------------------------


def test_a_pane_width_is_absent_until_remembered(main):
    widths = SqlitePaneWidthRepository(main)
    assert widths.width_percent("/project") is None
    widths.remember_width("/project", 40)
    assert widths.width_percent("/project") == 40


# test_toggling_a_view_opens_it_and_toggling_again_closes_it lived here. Which
# file views the mirror has expanded is the PANE's own state now: it holds every
# byte it draws, so expanding one needs nothing from the daemon and the daemon
# has no business remembering it.


# --- uploads ------------------------------------------------------------------


def test_expired_uploads_come_back_so_the_caller_can_unlink(main):
    uploads = SqliteUploadRepository(main)
    uploads.record(StoredUpload("u1", SESSION, "a.png", "image/png", 3, "/tmp/a.png", 1.0))
    uploads.record(StoredUpload("u2", None, "b.png", "image/png", 3, "/tmp/b.png", 100.0))
    removed = uploads.remove_expired(50.0)
    assert [upload.stored_path for upload in removed] == ["/tmp/a.png"]
    assert uploads.remove_expired(50.0) == ()


# --- audit --------------------------------------------------------------


def test_errors_are_written_and_counted_per_session(tmp_path):
    database = audit_database(str(tmp_path / "audit.db"))
    writes = SqliteAuditWriteRepository(database)
    reads = SqliteAuditReadRepository(read_only(database))
    writes.record_error(ApplicationErrorRecord(str(SESSION), "script", "where", "trace", "context", 1, 5.0))
    stored = reads.errors_for_session(SESSION)
    assert [error.action for error in stored] == ["where"]
    assert reads.error_counts() == {SESSION: 1}


def test_the_audit_writer_never_raises_when_its_file_is_unusable(tmp_path):
    unusable = audit_database(str(tmp_path / "missing" / "nested" / "audit.db"))
    unusable.path = str(tmp_path)  # a directory: every open will fail
    writes = SqliteAuditWriteRepository(unusable)
    writes.record_error(ApplicationErrorRecord("", "s", "f", "t", "c", 1, 1.0))
    writes.record_state_file(StateFileRecord("", "p", "a", "c", "s", 1, 1.0))
    writes.record_spawn(SpawnRecord("", "s", 2, "[]", "why", 1.0))
    assert writes.open_stream(StreamOpened("", "kind", "", "", "", 1, 1.0)) is None
    writes.close_stream(None, "done", 0)


def test_a_stream_row_is_opened_and_closed_through_its_handle(tmp_path):
    database = audit_database(str(tmp_path / "audit.db"))
    writes = SqliteAuditWriteRepository(database)
    handle = writes.open_stream(StreamOpened(str(SESSION), "mirror", "", "", "", 1, 1.0))
    assert handle is not None
    writes.close_stream(handle, "finished", 12)
    with database.read() as connection:
        row = connection.execute("SELECT * FROM streams WHERE id=?", (handle.stream_id,)).fetchone()
    assert (row["end_reason"], row["lines_emitted"]) == ("finished", 12)


def test_the_audit_is_a_no_op_when_switched_off(tmp_path, monkeypatch):
    monkeypatch.setenv("BAQYLAU_AUDIT", "0")
    database = audit_database(str(tmp_path / "audit.db"))
    writes = SqliteAuditWriteRepository(database)
    writes.record_error(ApplicationErrorRecord(str(SESSION), "s", "f", "t", "c", 1, 1.0))
    reads = SqliteAuditReadRepository(read_only(database))
    assert reads.errors_for_session(SESSION) == ()


# --- the clock is not the repository's ----------------------------------------


def test_nothing_in_the_layer_needs_a_real_clock_to_be_exercised(main):
    """Every timestamp above is supplied by the caller, which is why these
    tests assert on exact values rather than on ranges."""
    uploads = SqliteUploadRepository(main)
    uploads.record(StoredUpload("u", None, "n", "text/plain", 1, "/tmp/n", 0.0))
    assert uploads.remove_expired(time.time())[0].created_at == 0.0
