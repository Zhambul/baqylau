# Copyright (c) 2026 Zhambyl Yermagambet
"""Provide sqlite test entries."""

from __future__ import annotations

from tests import (
    sqlite_domain_dependencies as domain_dependencies,
    sqlite_library_dependencies as library_dependencies,
    sqlite_repository_dependencies as repository_dependencies,
    sqlite_test_dependencies as test_dependencies,
    sqlite_test_fixtures,
    sqlite_test_models,
    sqlite_test_shells,
    sqlite_value_dependencies as standard_dependencies,
)
from tests.harness_names import CLAUDE_CODE_HARNESS, CODEX_HARNESS

SESSION = domain_dependencies.domain_ids.SessionId("session-one")
SESSION_TEXT = str(SESSION)
ACTOR = domain_dependencies.domain_ids.ActorId("actor-one")
HARNESS = CODEX_HARNESS
FIRST_TRANSLATION_TIME = 1001.0
PROJECT_DIRECTORY = "/project"
FIRST_SOURCE_POSITION = "1"
THIRD_SOURCE_POSITION = "3"
COUNT_VALUE_FIELD = "value"
LEGACY_SHELL_SCHEMA_VERSION = 5
LEAD_ACTOR_ID_TEXT = "lead"
AN_ACTOR = standard_dependencies.actor_state.ActorFacts(
    session_id=SESSION,
    actor_id=domain_dependencies.domain_ids.ActorId(LEAD_ACTOR_ID_TEXT),
    role=domain_dependencies.messaging.ActorRole.LEAD,
    name="claude",
    state=domain_dependencies.lifecycle.LifecycleState.RUNNING,
)


def record_finished_background_shell(
    migration: sqlite_test_models.MigrationDatabase,
    schema_version: int = LEGACY_SHELL_SCHEMA_VERSION,
) -> domain_dependencies.domain_ids.ShellId:
    """Record a background shell completion before the output repair.

    Returns:
        The completed shell identifier.

    """
    raw = sqlite_test_fixtures.a_raw_event()
    test_dependencies.SqliteRawEventRepository(migration.old).record([raw])
    shell_id = domain_dependencies.domain_ids.ShellId("yielded-one")
    backgrounded = standard_dependencies.replace(
        sqlite_test_fixtures.a_started_event("backgrounded-one"),
        payload=library_dependencies.event_shell.ShellBackgrounded(shell_id),
    )
    finished = standard_dependencies.replace(
        sqlite_test_fixtures.a_started_event("finished-one"),
        payload=library_dependencies.event_shell.ShellFinished(
            shell_id,
            domain_dependencies.outcomes.Outcome.SUCCEEDED,
            library_dependencies.domain_content.TextContent("done"),
            0,
        ),
    )
    repository_dependencies.SqliteCanonicalEventRepository(migration.old).record_translation(
        raw,
        FIRST_SOURCE_POSITION,
        repository_dependencies.raw_event_models.TranslationResult(
            (backgrounded, finished),
            domain_dependencies.domain_records.RecordedTranslationDecision.TRANSLATED,
        ),
        FIRST_TRANSLATION_TIME,
    )
    with migration.old.write() as connection:
        if schema_version == LEGACY_SHELL_SCHEMA_VERSION:
            sqlite_test_fixtures.restore_version_six_queue_table(connection)
            sqlite_test_shells.restore_version_ten_schema(connection)
        connection.execute("UPDATE schema_version SET version = ? WHERE id = 1", (schema_version,))
    return shell_id


def store_version_seven_goal(migration: sqlite_test_models.MigrationDatabase) -> None:
    """Store a completed goal with the version seven payload fields."""
    facts = domain_dependencies.session_state.SessionFacts(
        session_id=SESSION,
        harness=HARNESS,
        state=domain_dependencies.lifecycle.LifecycleState.RUNNING,
        working_directory=PROJECT_DIRECTORY,
        started_at=1000.0,
        lead_actor_id=ACTOR,
        goal=domain_dependencies.session_state.SessionGoal(
            "Ship it",
            repository_dependencies.work_state.GoalState.COMPLETED,
            None,
        ),
    )
    with migration.old.write() as connection:
        connection.execute(
            "INSERT INTO session_data(session_id, revision, payload) VALUES(?, ?, ?)",
            (SESSION_TEXT, 1, test_dependencies.documents.encode_document(facts).decode()),
        )
        connection.execute(
            (
                "\n            UPDATE session_data\n            SET payload = json_set(\n      "
                "          json_remove(payload, '$.goal.state', '$.goal.reason'),\n          "
                "      '$.goal.completed', true\n            )\n            "
            ),
        )
        sqlite_test_shells.restore_version_ten_schema(connection)
        connection.execute("UPDATE schema_version SET version = 7 WHERE id = 1")


def record_version_eight_shell(
    migration: sqlite_test_models.MigrationDatabase,
) -> domain_dependencies.domain_ids.ShellId:
    """Record a shell completion after a turn with the version eight schema.

    Returns:
        The completed shell identifier.

    """
    raw = sqlite_test_fixtures.a_raw_event()
    test_dependencies.SqliteRawEventRepository(migration.old).record([raw])
    turn_finished = standard_dependencies.replace(
        sqlite_test_fixtures.a_started_event("turn-finished"),
        payload=library_dependencies.event_conversation.TurnFinished(
            None,
            domain_dependencies.outcomes.Outcome.SUCCEEDED,
        ),
    )
    shell_id = domain_dependencies.domain_ids.ShellId("late-parallel-command")
    shell_finished = standard_dependencies.replace(
        sqlite_test_fixtures.a_started_event("late-shell-finished"),
        payload=library_dependencies.event_shell.ShellFinished(
            shell_id,
            domain_dependencies.outcomes.Outcome.SUCCEEDED,
            library_dependencies.domain_content.TextContent("done"),
            0,
        ),
    )
    repository_dependencies.SqliteCanonicalEventRepository(migration.old).record_translation(
        raw,
        FIRST_SOURCE_POSITION,
        repository_dependencies.raw_event_models.TranslationResult(
            (turn_finished, shell_finished),
            domain_dependencies.domain_records.RecordedTranslationDecision.TRANSLATED,
        ),
        FIRST_TRANSLATION_TIME,
    )
    with migration.old.write() as connection:
        sqlite_test_shells.restore_version_ten_schema(connection)
        connection.execute("UPDATE schema_version SET version = 8 WHERE id = 1")
    return shell_id


class RestartedShellScenario:
    """Build the event order for one restarted shell migration."""

    def __init__(self) -> None:
        """Set the original and replacement shell identifiers and command."""
        self.original_shell = domain_dependencies.domain_ids.ShellId("call-before-restart")
        self.replacement_shell = domain_dependencies.domain_ids.ShellId("native-after-restart")
        self.command = library_dependencies.domain_content.TextContent("sleep 25")

    def events(
        self,
        *,
        backgrounded_after_replacement: bool,
        has_late_shell_finish: bool,
    ) -> tuple[
        library_dependencies.event_base.CanonicalEvent[library_dependencies.event_base.EventPayload],
        ...,
    ]:
        """Build shell events in the requested replacement order.

        Returns:
            The ordered events, with an optional late shell completion.

        """
        original_started = sqlite_test_shells.shell_started_event(
            "original-started",
            self.original_shell,
            self.command,
        )
        backgrounded = standard_dependencies.replace(
            sqlite_test_fixtures.a_started_event("original-backgrounded"),
            payload=library_dependencies.event_shell.ShellBackgrounded(self.original_shell),
        )
        replacement = (
            sqlite_test_shells.shell_started_event("replacement-started", self.replacement_shell, self.command),
            sqlite_test_shells.shell_finished_event(
                "replacement-finished",
                self.replacement_shell,
                domain_dependencies.outcomes.Outcome.SUCCEEDED,
                library_dependencies.domain_content.TextContent("done"),
            ),
        )
        ordered = (
            (original_started, *replacement, backgrounded)
            if backgrounded_after_replacement
            else (original_started, backgrounded, *replacement)
        )
        if has_late_shell_finish:
            return (
                *ordered,
                sqlite_test_shells.shell_finished_event(
                    "original-finished",
                    self.original_shell,
                    domain_dependencies.outcomes.Outcome.CANCELLED,
                    None,
                ),
            )
        return ordered

    def actor(self) -> standard_dependencies.actor_state.ActorFacts:
        """Build the actor state before the upgrade.

        Returns:
            The actor state before the upgrade.

        """
        return standard_dependencies.replace(
            AN_ACTOR,
            actor_id=ACTOR,
            background=standard_dependencies.actor_state.ActorBackground(running_shell_ids=(self.original_shell,)),
            status=standard_dependencies.actor_state.ActorStatus.AWAITING_BACKGROUND,
        )


def restore_version_seventeen_events(migration: sqlite_test_models.MigrationDatabase) -> None:
    """Store ignored TaskStop and unrelated hooks with schema version seventeen."""
    task_stop = standard_dependencies.replace(
        sqlite_test_fixtures.a_raw_event("task-stop"),
        harness=CLAUDE_CODE_HARNESS,
        source_name="PostToolUse",
        payload=b'{"hook_event_name":"PostToolUse","tool_name":"TaskStop","tool_input":{"task_id":"background-one"}}',
    )
    unrelated = standard_dependencies.replace(
        sqlite_test_fixtures.a_raw_event("unrelated-hook"),
        harness=CODEX_HARNESS,
        source_name="PostToolUse",
    )
    test_dependencies.SqliteRawEventRepository(migration.old).record([task_stop, unrelated])
    canonical = repository_dependencies.SqliteCanonicalEventRepository(migration.old)
    canonical.record_translation(
        task_stop,
        THIRD_SOURCE_POSITION,
        repository_dependencies.raw_event_models.TranslationResult(
            (),
            domain_dependencies.domain_records.RecordedTranslationDecision.IGNORED_NONSEMANTIC,
            "old TaskStop handling",
        ),
        FIRST_TRANSLATION_TIME,
    )
    canonical.record_translation(
        unrelated,
        THIRD_SOURCE_POSITION,
        repository_dependencies.raw_event_models.TranslationResult(
            (),
            domain_dependencies.domain_records.RecordedTranslationDecision.IGNORED_NONSEMANTIC,
            "unrelated harness",
        ),
        FIRST_TRANSLATION_TIME,
    )
    with migration.old.write() as connection:
        connection.execute("UPDATE schema_version SET version = 17 WHERE id = 1")


def restore_version_eighteen_search(migration: sqlite_test_models.MigrationDatabase) -> None:
    """Store legacy ToolSearch facts and read-model rows with schema version eighteen."""
    hook = standard_dependencies.replace(
        sqlite_test_fixtures.a_raw_event("tool-search-result"),
        harness=CLAUDE_CODE_HARNESS,
        source_name="PostToolUse",
        payload=b'{"hook_event_name":"PostToolUse","tool_name":"ToolSearch","tool_input":{"query":"select:Monitor"},"tool_response":{"matches":["Monitor"]}}',
    )
    old_event = library_dependencies.event_base.CanonicalEvent(
        event_id=domain_dependencies.domain_ids.CanonicalEventId("old-tool-search"),
        session_id=SESSION,
        actor_id=ACTOR,
        turn_id=None,
        parent_actor_id=None,
        harness=CLAUDE_CODE_HARNESS,
        occurred_at=1000.0,
        terminal_window_id=None,
        harness_process_id=None,
        payload=library_dependencies.event_resource.SearchPerformed(
            "ToolSearch",
            library_dependencies.domain_content.TextContent("select:Monitor"),
            library_dependencies.domain_content.StructuredContent("{}"),
            domain_dependencies.outcomes.Outcome.SUCCEEDED,
        ),
    )
    test_dependencies.SqliteRawEventRepository(migration.old).record([hook])
    repository_dependencies.SqliteCanonicalEventRepository(migration.old).record_translation(
        hook,
        THIRD_SOURCE_POSITION,
        repository_dependencies.raw_event_models.TranslationResult(
            (old_event,),
            domain_dependencies.domain_records.RecordedTranslationDecision.TRANSLATED,
        ),
        FIRST_TRANSLATION_TIME,
    )
    with migration.old.write() as connection:
        connection.execute(
            (
                "INSERT INTO session_entries(cursor, entry_id, session_id, entry_type, "
                "actor_id, payload) VALUES(1, 'old-search-entry', ?, 'search', ?, '{}')"
            ),
            (SESSION_TEXT, str(ACTOR)),
        )
        connection.execute(
            "INSERT INTO reaction_progress(id, canonical_cursor, updated_at) VALUES(1, 1, ?)",
            (FIRST_TRANSLATION_TIME,),
        )
        connection.execute("UPDATE schema_version SET version = 18 WHERE id = 1")


def assert_version_eighteen_rows_are_cleared(
    migration: sqlite_test_models.MigrationDatabase,
    upgraded: repository_dependencies.SqliteDatabase,
) -> None:
    """Check that the upgrade removes legacy search facts and read-model rows."""
    event_count = migration.row(
        upgraded,
        "SELECT COUNT(*) AS value FROM canonical_events WHERE event_id='old-tool-search'",
    )
    interpretation_count = migration.row(
        upgraded,
        "SELECT COUNT(*) AS value FROM interpretations WHERE raw_event_id='tool-search-result'",
    )
    entry_count = migration.row(upgraded, "SELECT COUNT(*) AS value FROM session_entries")
    progress_count = migration.row(upgraded, "SELECT COUNT(*) AS value FROM reaction_progress")
    assert event_count[COUNT_VALUE_FIELD] == 0
    assert interpretation_count[COUNT_VALUE_FIELD] == 0
    assert entry_count[COUNT_VALUE_FIELD] == 0
    assert progress_count[COUNT_VALUE_FIELD] == 0
