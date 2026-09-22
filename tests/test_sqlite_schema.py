# Copyright (c) 2026 Zhambyl Yermagambet
"""Provide test sqlite schema."""

from __future__ import annotations

from tests import (
    sqlite_domain_dependencies as domain_dependencies,
    sqlite_library_dependencies as library_dependencies,
    sqlite_repository_dependencies as repository_dependencies,
    sqlite_schema_fixture,
    sqlite_test_dependencies as test_dependencies,
    sqlite_value_dependencies as standard_dependencies,
)

# Keep dependency modules separate from migration helpers.
# isort: split

from tests import (
    sqlite_test_entries,
    sqlite_test_events,
    sqlite_test_fixtures,
    sqlite_test_models,
    sqlite_test_shells,
)

SESSION = domain_dependencies.domain_ids.SessionId("session-one")
SESSION_TEXT = str(SESSION)
MAIN_DATABASE_NAME = "main.db"
SCHEMA_VERSION_FIELD = "version"
SCHEMA_VERSION_QUERY = "SELECT version FROM schema_version WHERE id = 1"
PAYLOAD_FIELD = "payload"
FIRST_REQUEST_ID = "request-one"
FIRST_MESSAGE_TEXT = "one"


def test_schema_is_applied_once_and_version(main: repository_dependencies.SqliteDatabase) -> None:
    """Verify the schema is applied once and the version is recorded."""
    main.initialize()
    main.initialize()
    with main.read() as connection:
        row = connection.execute("SELECT version FROM schema_version WHERE id=1").fetchone()
    assert row[SCHEMA_VERSION_FIELD] == main.schema_version


def test_file_written_by_another_schema_version(tmp_path: standard_dependencies.Path) -> None:
    """Verify a file written by another schema version refuses to open."""
    first = repository_dependencies.sqlite_databases.main_database(str(tmp_path / MAIN_DATABASE_NAME))
    first.initialize()
    second = repository_dependencies.SqliteDatabase(first.path, first.schema, first.schema_version + 1)
    with standard_dependencies.pytest.raises(repository_dependencies.repository_errors.SchemaVersionMismatchError):
        second.initialize()


def test_main_schema_is_created_whole_at_current(tmp_path: standard_dependencies.Path) -> None:
    """Verify the main schema is created whole at the current version."""
    database = repository_dependencies.sqlite_databases.main_database(str(tmp_path / MAIN_DATABASE_NAME))
    database.initialize()
    with database.read() as connection:
        version = connection.execute("SELECT version FROM schema_version WHERE id=1").fetchone()
        tables = {row["name"] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert version[SCHEMA_VERSION_FIELD] == test_dependencies.MAIN_SCHEMA_VERSION
    assert {"raw_events", "pending_raw_events", "canonical_events", "interpretations", "shell_output"} <= tables


def test_version_four_actor_models_are_migrated(migration: sqlite_test_models.MigrationDatabase) -> None:
    """Verify version four actor models are migrated to the domain shape."""
    actor = sqlite_test_events.store_version_four_actor(migration)
    upgraded = migration.upgrade()
    row = migration.row(upgraded, "SELECT payload FROM session_data_actors WHERE actor_id = ?", (str(actor.actor_id),))
    restored = test_dependencies.documents.decode_document(
        standard_dependencies.actor_state.ActorFacts, row[PAYLOAD_FIELD],
    )
    assert restored.model == actor.model
    assert "native_id" not in row[PAYLOAD_FIELD]
    assert "selection_id" not in row[PAYLOAD_FIELD]


@standard_dependencies.pytest.mark.parametrize("schema_version", [5, 25])
def test_upgrade_closes_finished_codex_shells(
    migration: sqlite_test_models.MigrationDatabase, schema_version: int,
) -> None:
    """Verify that an upgrade closes finished Codex background shells."""
    shell_id = sqlite_test_entries.record_finished_background_shell(migration, schema_version)
    upgraded = migration.upgrade()
    repaired = repository_dependencies.SqliteCanonicalEventRepository(upgraded).find(
        domain_dependencies.domain_ids.CanonicalEventId("migration:6:shell-output-finished:finished-one"),
    )
    assert repaired is not None
    assert repaired.payload == library_dependencies.event_shell.ShellOutputFinished(
        shell_id, domain_dependencies.outcomes.Outcome.SUCCEEDED,
    )


def test_version_six_queued_messages_gain_stable(tmp_path: standard_dependencies.Path) -> None:
    """Verify version six queued messages gain stable request identities."""
    database_path = str(tmp_path / MAIN_DATABASE_NAME)
    old_database = sqlite_schema_fixture.baseline_database(database_path)
    old_database.initialize()
    with old_database.write() as connection:
        connection.execute(
            "INSERT INTO session_workspaces(session_id, queue_origin) VALUES(?, ?)", (SESSION_TEXT, "browser"),
        )
        connection.executemany(
            "INSERT INTO composer_queue_items(session_id, position, request_id, text) VALUES(?, ?, ?, ?)",
            ((SESSION_TEXT, 0, FIRST_REQUEST_ID, FIRST_MESSAGE_TEXT), (SESSION_TEXT, 1, "request-two", "two")),
        )
        sqlite_test_fixtures.restore_version_six_queue_table(connection)
        sqlite_test_shells.restore_version_ten_schema(connection)
        connection.execute("UPDATE schema_version SET version = 6 WHERE id = 1")
    upgraded = repository_dependencies.sqlite_databases.main_database(database_path)
    upgraded.initialize()
    stored = test_dependencies.SqliteSessionWorkspaceRepository(upgraded).find(SESSION)
    assert stored is not None
    assert stored.queue is not None
    assert [message.request_id for message in stored.queue.messages] == ["legacy:0", "legacy:1"]
    with upgraded.read() as connection:
        assert (
            connection.execute(SCHEMA_VERSION_QUERY).fetchone()[SCHEMA_VERSION_FIELD]
            == test_dependencies.MAIN_SCHEMA_VERSION
        )


def test_version_seven_goals_gain_complete_state(migration: sqlite_test_models.MigrationDatabase) -> None:
    """Verify version seven goals gain the complete state shape."""
    sqlite_test_entries.store_version_seven_goal(migration)
    upgraded = migration.upgrade()
    row = migration.row(upgraded, "SELECT payload FROM session_data WHERE session_id = ?", (SESSION_TEXT,))
    restored = test_dependencies.documents.decode_document(
        domain_dependencies.session_state.SessionFacts, row[PAYLOAD_FIELD],
    )
    assert restored.goal is not None
    assert restored.goal.state == repository_dependencies.work_state.GoalState.COMPLETED
    assert restored.goal.reason is None
