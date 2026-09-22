# Copyright (c) 2026 Zhambyl Yermagambet
"""Provide test sqlite migrations."""

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
    sqlite_test_preferences,
)

RESUMED_HARNESS_PROCESS_ID = 202
MAIN_DATABASE_NAME = "main.db"
SCHEMA_VERSION_FIELD = "version"
SCHEMA_VERSION_QUERY = "SELECT version FROM schema_version WHERE id = 1"
RESUMED_RUN_SCHEMA_VERSION = 15


def test_version_eight_settles_codex_shell(migration: sqlite_test_models.MigrationDatabase) -> None:
    """Verify version eight settles codex shell finishes added after the turn."""
    shell_id = sqlite_test_entries.record_version_eight_shell(migration)
    upgraded = migration.upgrade()
    repaired = repository_dependencies.SqliteCanonicalEventRepository(upgraded).find(
        domain_dependencies.domain_ids.CanonicalEventId("migration:9:shell-settled:late-shell-finished"),
    )
    assert repaired is not None
    assert repaired.payload == library_dependencies.event_shell.ShellOutputFinished(
        shell_id, domain_dependencies.outcomes.Outcome.SUCCEEDED,
    )


@standard_dependencies.pytest.mark.parametrize(
    ("old_version", "backgrounded_after_replacement", "has_late_shell_finish"),
    [(14, False, False), (20, True, False), (21, True, True)],
)
def test_schema_upgrade_closes_codex_shell(
    migration: sqlite_test_models.MigrationDatabase,
    old_version: int,
    *,
    backgrounded_after_replacement: bool,
    has_late_shell_finish: bool,
) -> None:
    """Verify schema upgrade closes a codex shell duplicated after restart."""
    original_shell = sqlite_test_preferences.restore_restarted_shell(
        migration, old_version,
        backgrounded_after_replacement=backgrounded_after_replacement,
        has_late_shell_finish=has_late_shell_finish,
    )
    repaired = repository_dependencies.SqliteCanonicalEventRepository(migration.upgrade()).find(
        domain_dependencies.domain_ids.CanonicalEventId(
            "migration:15:recovered-shell-output-finished:original-backgrounded",
        ),
    )
    assert repaired is not None
    assert repaired.payload == library_dependencies.event_shell.ShellOutputFinished(
        original_shell, domain_dependencies.outcomes.Outcome.SUCCEEDED,
    )


def test_version_fifteen_finishes_resumed_run(migration: sqlite_test_models.MigrationDatabase) -> None:
    """Verify version fifteen finishes a resumed run with a deduplicated exit."""
    scenario = sqlite_test_events.ResumedRunScenario()
    scenario.record(migration.old)
    migration.set_version(RESUMED_RUN_SCHEMA_VERSION)
    upgraded = migration.upgrade()
    repaired = repository_dependencies.SqliteCanonicalEventRepository(upgraded).find(
        domain_dependencies.domain_ids.CanonicalEventId("migration:16:session-run-finished:resumed-exit"),
    )
    assert repaired is not None
    assert repaired.terminal_window_id == scenario.resumed_window
    assert repaired.harness_process_id == RESUMED_HARNESS_PROCESS_ID
    assert repaired.payload == library_dependencies.event_session.SessionFinished(
        domain_dependencies.outcomes.Outcome.UNKNOWN, "process_exited",
    )
    assert not test_dependencies.SqliteSessionRepository(upgraded).watchable()


def test_version_sixteen_adds_covering_session(tmp_path: standard_dependencies.Path) -> None:
    """Verify version sixteen adds the covering session activity index."""
    database_path = str(tmp_path / MAIN_DATABASE_NAME)
    old_database = sqlite_schema_fixture.baseline_database(database_path)
    old_database.initialize()
    with old_database.write() as connection:
        connection.execute("DROP INDEX index_session_entries_session")
        connection.execute("CREATE INDEX index_session_entries_session ON session_entries(session_id, cursor)")
        connection.execute("UPDATE schema_version SET version = 16 WHERE id = 1")
    upgraded = repository_dependencies.sqlite_databases.main_database(database_path)
    upgraded.initialize()
    with upgraded.read() as connection:
        columns = tuple(row["name"] for row in connection.execute("PRAGMA index_info(index_session_entries_session)"))
        assert (
            connection.execute(SCHEMA_VERSION_QUERY).fetchone()[SCHEMA_VERSION_FIELD]
            == test_dependencies.MAIN_SCHEMA_VERSION
        )
    assert columns == ("session_id", "cursor", "occurred_at")


def test_version_seventeen_requeues_ignored(migration: sqlite_test_models.MigrationDatabase) -> None:
    """Verify version seventeen requeues ignored claude post tool hooks."""
    sqlite_test_entries.restore_version_seventeen_events(migration)
    upgraded = migration.upgrade()
    assert [event.raw_event_id for event in test_dependencies.SqliteRawEventRepository(upgraded).unverdicted(10)] == [
        domain_dependencies.domain_ids.RawEventId("task-stop"),
    ]
    remaining = migration.rows(upgraded, "SELECT raw_event_id FROM interpretations ORDER BY raw_event_id")
    assert [row["raw_event_id"] for row in remaining] == ["unrelated-hook"]


def test_version_eighteen_reprocesses_structured(migration: sqlite_test_models.MigrationDatabase) -> None:
    """Verify version eighteen reprocesses structured claude search results."""
    sqlite_test_entries.restore_version_eighteen_search(migration)
    upgraded = migration.upgrade()
    assert [event.raw_event_id for event in test_dependencies.SqliteRawEventRepository(upgraded).unverdicted(10)] == [
        domain_dependencies.domain_ids.RawEventId("tool-search-result"),
    ]
    sqlite_test_entries.assert_version_eighteen_rows_are_cleared(migration, upgraded)


def test_version_nineteen_normalizes_canon_model(migration: sqlite_test_models.MigrationDatabase) -> None:
    """Verify version nineteen normalizes canonical model references."""
    sqlite_test_preferences.restore_version_nineteen_models(migration)
    upgraded = migration.upgrade()
    sqlite_test_fixtures.assert_normalized_model_rows(
        sqlite_test_preferences.version_nineteen_rows(migration, upgraded),
    )
