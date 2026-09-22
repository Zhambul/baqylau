# Copyright (c) 2026 Zhambyl Yermagambet
"""Hold a real migration while a second initializer requests the write lock."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event

import pytest

from repository.impl.sqlite.connection import SqliteDatabase
from tests import sqlite_migration_fixture as fixtures


@dataclass(frozen=True)
class MigrationBarrier:
    """Use explicit events, not delays, to control two independent connections."""

    path: str
    paused: Event = field(default_factory=Event)
    released: Event = field(default_factory=Event)
    attempted: Event = field(default_factory=Event)

    @contextmanager
    def release_after(self) -> Iterator[None]:
        """Release the writer if a test assertion fails.

        Yields:
            A test section with guaranteed writer release.

        """
        try:
            yield
        finally:
            self.released.set()

    def writer(self) -> sqlite3.Connection:
        """Open a connection that can pause inside the test migration.

        Returns:
            A real connection owned by the schema runner.

        """
        connection = self._connect()
        connection.create_function("hold_upgrade", 0, self._pause)
        return connection

    def waiter(self) -> sqlite3.Connection:
        """Observe the second initializer's lock request.

        Returns:
            A real connection with a statement trace.

        """
        connection = self._connect()
        connection.set_trace_callback(self._trace)
        return connection

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _pause(self) -> int:
        self.paused.set()
        assert self.released.wait(5)
        return 1

    def _trace(self, statement: str) -> None:
        if statement == "BEGIN IMMEDIATE":
            self.attempted.set()


@dataclass(frozen=True)
class MigrationRace:
    """Keep the old reader, two initializers, and their test barriers together."""

    original: SqliteDatabase
    first: SqliteDatabase
    second: SqliteDatabase
    barrier: MigrationBarrier


def race(directory: Path, monkeypatch: pytest.MonkeyPatch, *, fail_first: bool) -> MigrationRace:
    """Arrange two real schema initializers for one file.

    Returns:
        Initializers that use the production migration runner and lock policy.

    """
    original = fixtures.populated(directory)
    statements: tuple[str, ...] = (*fixtures.COPY_MIGRATION, "SELECT hold_upgrade()")
    if fail_first:
        statements = (*statements, "INSERT INTO missing_migration_table VALUES(1)")
    case = MigrationRace(
        original, fixtures.upgrade(original, statements),
        fixtures.upgrade(original, fixtures.COPY_MIGRATION), MigrationBarrier(original.path),
    )
    monkeypatch.setattr(case.first, "_connect", case.barrier.writer)
    monkeypatch.setattr(case.second, "_connect", case.barrier.waiter)
    return case
