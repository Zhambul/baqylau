# Copyright (c) 2026 Zhambyl Yermagambet
"""Provide test sqlite shells."""

from __future__ import annotations

from tests import (
    sqlite_domain_dependencies as domain_dependencies,
    sqlite_repository_dependencies as repository_dependencies,
    sqlite_test_dependencies as test_dependencies,
    sqlite_value_dependencies as standard_dependencies,
)
from tests.harness_names import CODEX_HARNESS

# Keep dependency modules separate from migration helpers.
# isort: split

from tests import (
    sqlite_test_fixtures,
    sqlite_test_migrations,
    sqlite_test_models,
    sqlite_test_preferences,
    sqlite_test_shells,
)

SESSION = domain_dependencies.domain_ids.SessionId("session-one")
SESSION_TEXT = str(SESSION)
HARNESS = CODEX_HARNESS


def test_version_nine_builds_pending_raw_event(migration: sqlite_test_models.MigrationDatabase) -> None:
    """Verify version nine builds the pending raw event queue."""
    sqlite_test_preferences.restore_version_nine_events(migration)
    upgraded = migration.upgrade()
    assert [event.raw_event_id for event in test_dependencies.SqliteRawEventRepository(upgraded).unverdicted(10)] == [
        domain_dependencies.domain_ids.RawEventId("raw-pending"),
    ]


def test_version_eleven_builds_session_lifecycle(migration: sqlite_test_models.MigrationDatabase) -> None:
    """Verify version eleven builds the session lifecycle index."""
    sqlite_test_preferences.restore_version_eleven_session(migration)
    upgraded = migration.upgrade()
    assert not test_dependencies.SqliteSessionRepository(upgraded).watchable()
    session = migration.row(upgraded, "SELECT lifecycle FROM sessions WHERE session_id = ?", (SESSION_TEXT,))
    assert session["lifecycle"] == "finished"


def test_version_twelve_adds_stable_project(migration: sqlite_test_models.MigrationDatabase) -> None:
    """Verify version twelve adds the stable project identity."""
    sqlite_test_preferences.restore_version_twelve_session(migration)
    upgraded = migration.upgrade()
    stored = test_dependencies.SqliteSessionRepository(upgraded).find(SESSION)
    assert stored
    assert stored.project_directory is None
    assert "project_directory" in sqlite_test_migrations.session_columns(upgraded)


def test_read_only_database_never_creates_file(tmp_path: standard_dependencies.Path) -> None:
    """Verify a read only database never creates the file."""
    forensic = repository_dependencies.sqlite_databases.read_only(
        repository_dependencies.sqlite_databases.main_database(str(tmp_path / "absent.db")),
    )
    forensic.initialize()
    assert not forensic.exists()


def test_failed_write_rolls_whole_transaction(main: repository_dependencies.SqliteDatabase) -> None:
    """Verify a failed write rolls the whole transaction back."""
    sessions = test_dependencies.SqliteSessionRepository(main)
    sessions.save(HARNESS, sqlite_test_fixtures.a_session())
    with standard_dependencies.pytest.raises(RuntimeError):
        sqlite_test_models.fail_database_write(main)
    assert sessions.find(SESSION) is not None


def test_repo_transactions_reuse_one_connection(
    main: repository_dependencies.SqliteDatabase, monkeypatch: standard_dependencies.pytest.MonkeyPatch,
) -> None:
    """Verify repository transactions reuse one connection per thread."""
    main.initialize()
    opened_connections: list[standard_dependencies.sqlite3.Connection] = []
    native_connect = main._connect  # noqa: SLF001 -- Count actual connection creation in the reuse test.
    monkeypatch.setattr(
        main, "_connect", lambda: sqlite_test_shells.track_connection(opened_connections, native_connect),
    )
    with main.read() as connection_one:
        connection_one.execute("SELECT 1").fetchone()
    with main.read() as connection_two:
        connection_two.execute("SELECT 1").fetchone()
    with main.write() as connection_three:
        connection_three.execute("SELECT 1").fetchone()
    assert connection_one is connection_two
    assert connection_two is connection_three
    assert opened_connections == [connection_one]


def test_nested_repo_transactions_fail(main: repository_dependencies.SqliteDatabase) -> None:
    """Verify nested repository transactions fail before the outer transaction changes."""
    with (
        main.read(),
        standard_dependencies.pytest.raises(RuntimeError, match="nested SQLite repository transaction"), main.write(),
    ):
        standard_dependencies.pytest.fail("nested write did not fail")
