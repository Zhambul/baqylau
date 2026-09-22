# Copyright (c) 2026 Zhambyl Yermagambet
"""Provide sqlite test models."""

from __future__ import annotations

from tests import (
    sqlite_repository_dependencies as repository_dependencies,
    sqlite_schema_fixture,
    sqlite_test_dependencies as test_dependencies,
    sqlite_value_dependencies as standard_dependencies,
)

MAIN_DATABASE_NAME = "main.db"
SCHEMA_VERSION_FIELD = "version"
SCHEMA_VERSION_QUERY = "SELECT version FROM schema_version WHERE id = 1"


class NativeConnect(standard_dependencies.typing.Protocol):
    """Define a function that opens a SQLite connection."""

    def __call__(self) -> standard_dependencies.sqlite3.Connection:
        """Open a connection.

        Returns:
            The new SQLite connection.

        """


class MigrationDatabase:
    """Manage the old and upgraded views of one database file."""

    def __init__(self, tmp_path: standard_dependencies.Path) -> None:
        """Create and initialize the database in the test directory."""
        self.path = str(tmp_path / MAIN_DATABASE_NAME)
        self.old = sqlite_schema_fixture.baseline_database(self.path)
        self.old.initialize()

    def set_version(self, version: int) -> None:
        """Set the source schema version."""
        with self.old.write() as connection:
            connection.execute("UPDATE schema_version SET version = ? WHERE id = 1", (version,))

    def upgrade(self) -> repository_dependencies.SqliteDatabase:
        """Open a new database object and apply all later migrations.

        Returns:
            The database after verification of its current schema version.

        """
        upgraded = repository_dependencies.sqlite_databases.main_database(self.path)
        upgraded.initialize()
        self.assert_current_version(upgraded)
        return upgraded

    def row(
        self,
        database: repository_dependencies.SqliteDatabase,
        query: str,
        query_parameters: tuple[object, ...] = (),
    ) -> standard_dependencies.sqlite3.Row:
        """Read one required row from a database.

        Returns:
            The first row returned by the query.

        """
        with database.read() as connection:
            row = connection.execute(query, query_parameters).fetchone()
        assert row is not None
        return row

    def rows(
        self,
        database: repository_dependencies.SqliteDatabase,
        query: str,
        query_parameters: tuple[object, ...] = (),
    ) -> list[standard_dependencies.sqlite3.Row]:
        """Read all rows for one query.

        Returns:
            The complete list of query rows.

        """
        with database.read() as connection:
            return connection.execute(query, query_parameters).fetchall()

    def assert_current_version(self, database: repository_dependencies.SqliteDatabase) -> None:
        """Verify that an upgrade reached the current schema."""
        assert self.row(database, SCHEMA_VERSION_QUERY)[SCHEMA_VERSION_FIELD] == test_dependencies.MAIN_SCHEMA_VERSION


def fail_database_write(
    database: repository_dependencies.SqliteDatabase,
) -> standard_dependencies.typing.Never:
    """Fail after a write to check transaction rollback.

    Raises:
        RuntimeError: After deleting the session rows in the transaction.

    """
    with database.write() as connection:
        connection.execute("DELETE FROM sessions")
        message = "boom"
        raise RuntimeError(message)


def connection_identity(
    database: repository_dependencies.SqliteDatabase,
    barrier: standard_dependencies.Barrier,
    _worker_number: int,
) -> int:
    """Read a connection identity while all test workers hold a connection.

    Returns:
        The identity of this worker's connection.

    """
    with database.read() as connection:
        barrier.wait()
        return id(connection)


@standard_dependencies.pytest.fixture
def main(tmp_path: standard_dependencies.Path) -> repository_dependencies.SqliteDatabase:
    """Create the main database object for one test.

    Returns:
        The database object for the test's temporary directory.

    """
    return repository_dependencies.sqlite_databases.main_database(str(tmp_path / MAIN_DATABASE_NAME))


def raw_event_repository(
    main_database: repository_dependencies.SqliteDatabase,
) -> test_dependencies.SqliteRawEventRepository:
    """Build the raw-event repository for one test database.

    Returns:
        The raw-event repository for one test database.

    """
    return test_dependencies.SqliteRawEventRepository(main_database)


def session_data_repository(
    main_database: repository_dependencies.SqliteDatabase,
) -> test_dependencies.SqliteSessionDataRepository:
    """Build the session-data repository for one test database.

    Returns:
        The session-data repository for one test database.

    """
    return test_dependencies.SqliteSessionDataRepository(main_database)
