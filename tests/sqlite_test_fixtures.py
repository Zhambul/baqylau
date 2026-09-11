# Copyright (c) 2026 Zhambyl Yermagambet
"""Provide sqlite test fixtures."""

from __future__ import annotations

from tests import (
    sqlite_domain_dependencies as domain_dependencies,
    sqlite_library_dependencies as library_dependencies,
    sqlite_repository_dependencies as repository_dependencies,
    sqlite_value_dependencies as standard_dependencies,
)
from tests.harness_names import CODEX_HARNESS

SESSION = domain_dependencies.domain_ids.SessionId("session-one")
ACTOR = domain_dependencies.domain_ids.ActorId("actor-one")
HARNESS = CODEX_HARNESS
PROJECT_DIRECTORY = "/project"
FIRST_RAW_EVENT_ID = "raw-one"
FIRST_SOURCE_POSITION = "1"
FIRST_CANONICAL_EVENT_ID = "event-one"
NORMALIZED_MODEL_ROW_COUNT = 3


def a_session(
    terminal_window_id: domain_dependencies.domain_ids.WindowId | None = None,
    harness_process_id: int | None = None,
) -> repository_dependencies.Session:
    """Build a session with optional terminal and process identities.

    Returns:
        The fixed session for repository tests.

    """
    return repository_dependencies.Session(
        session_id=SESSION,
        lead_actor_id=ACTOR,
        source_reference="/transcripts/one.jsonl",
        working_directory=PROJECT_DIRECTORY,
        terminal_window_id=terminal_window_id,
        harness_process_id=harness_process_id,
    )


def a_raw_event(
    identity: str = FIRST_RAW_EVENT_ID,
    position: str = FIRST_SOURCE_POSITION,
) -> repository_dependencies.raw_event_models.RawEvent:
    """Build a raw hook event with the supplied identity and position.

    Returns:
        The event for the fixed session and actor.

    """
    return repository_dependencies.raw_event_models.RawEvent(
        raw_event_id=domain_dependencies.domain_ids.RawEventId(identity),
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


def a_started_event(
    event_id: str = FIRST_CANONICAL_EVENT_ID,
) -> library_dependencies.event_base.CanonicalEvent:
    """Build a canonical session-start event.

    Returns:
        The event for the fixed project and transcript path.

    """
    return library_dependencies.event_base.CanonicalEvent(
        event_id=domain_dependencies.domain_ids.CanonicalEventId(event_id),
        session_id=SESSION,
        actor_id=ACTOR,
        turn_id=None,
        parent_actor_id=None,
        harness=HARNESS,
        occurred_at=1000.0,
        terminal_window_id=None,
        harness_process_id=None,
        payload=library_dependencies.event_session.SessionStarted(
            PROJECT_DIRECTORY,
            "/transcripts/one.jsonl",
            None,
            None,
            None,
            None,
            None,
        ),
    )


def restore_version_six_queue_table(connection: standard_dependencies.sqlite3.Connection) -> None:
    """Restore the version six queue table and keep its stored items."""
    connection.execute("DROP INDEX index_composer_queue_request")
    connection.execute("ALTER TABLE composer_queue_items RENAME TO composer_queue_items_v7")
    connection.execute(
        (
            "\n        CREATE TABLE composer_queue_items(\n            session_id TEXT "
            "NOT NULL,\n            position INTEGER NOT NULL,\n            text TEXT NOT "
            "NULL,\n            PRIMARY KEY(session_id, position),\n            FOREIGN "
            "KEY(session_id) REFERENCES session_workspaces(session_id)\n                "
            "ON DELETE CASCADE\n        )\n        "
        ),
    )
    connection.execute(
        (
            "INSERT INTO composer_queue_items(session_id, position, text) SELECT "
            "session_id, position, text FROM composer_queue_items_v7"
        ),
    )
    connection.execute("DROP TABLE composer_queue_items_v7")


def restore_version_eleven_schema(connection: standard_dependencies.sqlite3.Connection) -> None:
    """Remove session fields and triggers added after version eleven."""
    connection.execute("DROP TRIGGER sessions_lifecycle_after_event")
    connection.execute("DROP TRIGGER sessions_lifecycle_after_insert")
    connection.execute("ALTER TABLE sessions DROP COLUMN lifecycle")
    connection.execute("ALTER TABLE sessions DROP COLUMN project_directory")


def shell_exit_code(content: library_dependencies.domain_content.TextContent | None) -> int | None:
    """Choose an exit code for the test shell result.

    Returns:
        Zero if content is present, or None if it is absent.

    """
    if content is None:
        return None
    return 0


def assert_normalized_model_rows(rows: list[standard_dependencies.sqlite3.Row]) -> None:
    """Check the model names and native values after migration."""
    assert len(rows) == NORMALIZED_MODEL_ROW_COUNT
    for row in rows:
        name_field = "current_name" if row["event_type"] == "model.changed" else "model_name"
        native_field = "current_native" if row["event_type"] == "model.changed" else "model_native"
        assert row[name_field] == "claude-fable-5"
        assert row[native_field] is None
    model_row = next(event_row for event_row in rows if event_row["event_type"] == "model.changed")
    assert model_row["previous_name"] == "claude-fable-5"
