# Copyright (c) 2026 Zhambyl Yermagambet
"""One connection policy, one initialize, for every database we own.

Before this there were six policies: WAL or not, timeouts from 0.2 s to 10 s,
read-only URIs next to read-write opens, `foreign_keys` on in one file and off
in the rest, and schemas applied either on every connect or on the first write.
Four separate objects each called `initialize()` on the SAME file, so the schema
was applied four times per process start.

`read()` and `write()` are INTERNAL to this package. They appear on no Protocol,
so nothing above the contract line can hold a connection or manage a
transaction — a repository method is one whole transaction, decided here.
"""

from __future__ import annotations

import pathlib
import sqlite3
import time
from contextlib import closing, contextmanager
from dataclasses import dataclass
from threading import Event, Lock, local
from types import MappingProxyType
from typing import TYPE_CHECKING

from repository.errors import SchemaVersionMismatchError

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

    from core import change_signal, work_queue

EMPTY_MIGRATIONS: Mapping[int, tuple[str, ...]] = MappingProxyType({})
AUDIT_BUSY_TIMEOUT_MILLISECONDS = 5_000
AUDIT_TIMEOUT_SECONDS = 5.0
PRIVATE_DIRECTORY_MODE = 0o700


def _ensure_parent(path: str) -> None:
    parent = pathlib.Path(path).parent
    parent.mkdir(mode=PRIVATE_DIRECTORY_MODE, exist_ok=True, parents=True)


def _version_message(path: str, stored_version: int, expected_version: int) -> str:
    filename = pathlib.Path(path).name
    return f"{filename} was written by schema version {stored_version}, this build expects {expected_version}"


def _stored_version(connection: sqlite3.Connection) -> int | None:
    table = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_version'",
    ).fetchone()
    if table is None:
        return None
    row = connection.execute("SELECT version FROM schema_version WHERE id=1").fetchone()
    return None if row is None else int(row["version"])


def _commit_write(connection: sqlite3.Connection) -> None:
    try:
        connection.commit()
    except BaseException:
        connection.rollback()
        raise


@dataclass(frozen=True)
class SqlitePragmas:
    """The default is the event store's policy — the one deliberate one."""

    journal_mode: str | None = "WAL"
    foreign_keys: bool = True
    busy_timeout_milliseconds: int = 10_000
    timeout_seconds: float = 10.0
    read_only: bool = False
    file_mode: int | None = 0o600


# A module singleton rather than a default constructed per call: the same policy
# object for every database that does not ask for a different one.
DEFAULT_PRAGMAS = SqlitePragmas()

# The audit database is written from inside `except` blocks in short-lived hook
# processes; it must fail fast rather than hold one up, and it must never be the
# reason a hook is slow.
AUDIT_PRAGMAS = SqlitePragmas(
    busy_timeout_milliseconds=AUDIT_BUSY_TIMEOUT_MILLISECONDS,
    timeout_seconds=AUDIT_TIMEOUT_SECONDS,
)

READ_ONLY_PRAGMAS = SqlitePragmas(read_only=True, file_mode=None)


class SqliteSchemaManager:
    """Apply and verify one SQLite schema."""

    path: str
    schema: str
    schema_version: int
    sqlite_pragmas: SqlitePragmas
    _migrations: Mapping[int, tuple[str, ...]]

    def _connect(self) -> sqlite3.Connection:
        raise NotImplementedError

    def _apply_schema(self) -> None:
        _ensure_parent(self.path)
        with closing(self._connect()) as connection:
            if self.sqlite_pragmas.journal_mode:
                journal_mode = self.sqlite_pragmas.journal_mode
                connection.execute(f"PRAGMA journal_mode={journal_mode}")
            self._migrate(connection)
            connection.executescript(self.schema)
            self._verify_version(connection)
            connection.commit()
        if self.sqlite_pragmas.file_mode is not None:
            pathlib.Path(self.path).chmod(self.sqlite_pragmas.file_mode)

    def _verify_version(self, connection: sqlite3.Connection) -> None:
        row = connection.execute("SELECT version FROM schema_version WHERE id=1").fetchone()
        if row is None:
            connection.execute(
                "INSERT INTO schema_version(id, version, applied_at) VALUES(1, ?, ?)",
                (self.schema_version, time.time()),
            )
            return
        if int(row["version"]) != self.schema_version:
            message = _version_message(self.path, int(row["version"]), self.schema_version)
            raise SchemaVersionMismatchError(message)

    def _migrate(self, connection: sqlite3.Connection) -> None:
        # The connection context does not start a transaction before DDL.
        # Lock before reading the version so concurrent owners cannot apply
        # the same migration. Publish the complete pending chain together.
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            stored_version = _stored_version(connection)
            if stored_version is not None:
                self._migrate_from_version(connection, stored_version)

    def _migrate_from_version(self, connection: sqlite3.Connection, stored_version: int) -> None:
        if stored_version > self.schema_version:
            raise self._version_mismatch(stored_version)
        for target_version in range(stored_version + 1, self.schema_version + 1):
            statements = self._migrations.get(target_version)
            if statements is None:
                raise self._version_mismatch(stored_version)
            for statement in statements:
                connection.execute(statement)
            connection.execute(
                "UPDATE schema_version SET version=?, applied_at=? WHERE id=1",
                (target_version, time.time()),
            )

    def _version_mismatch(self, stored_version: int) -> SchemaVersionMismatchError:
        return SchemaVersionMismatchError(_version_message(self.path, stored_version, self.schema_version))


class SqliteDatabase(SqliteSchemaManager):
    """Manage SQLite connections and repository transactions.

    Each thread keeps one connection because SQLite connections are thread-bound.
    Event processing can call several repository methods for one change batch.
    Connection reuse avoids opening the files and reading the schema each time.
    Each repository method keeps its own transaction.
    """

    def __init__(
        self,
        path: str,
        schema: str,
        schema_version: int,
        sqlite_pragmas: SqlitePragmas = DEFAULT_PRAGMAS,
        migrations: Mapping[int, tuple[str, ...]] | None = None,
    ) -> None:
        """Initialize the object."""
        self.path = str(pathlib.Path(path).resolve())
        self.schema = schema
        self.schema_version = schema_version
        self.sqlite_pragmas = sqlite_pragmas
        self._migrations = migrations or EMPTY_MIGRATIONS
        # An Event, not a bool: the fast path is a read the type checker
        # cannot narrow, which is exactly right — another thread may set it
        # between the two checks below, and that is the point of them.
        self._initialized = Event()
        self._initialize_lock = Lock()
        self._thread = local()
        self.work_queue: work_queue.WorkQueue | None = None
        self.changes: change_signal.ChangeSignal | None = None

    def exists(self) -> bool:
        """Return the exists.

        Returns:
            Exists.

        """
        return pathlib.Path(self.path).is_file()

    def initialize(self) -> None:
        """Create or verify the file. Idempotent, and cheap after the first call."""
        if self._initialized.is_set():
            return
        with self._initialize_lock:
            if self._initialized.is_set():
                return  # a peer thread won the lock and did the work
            # A forensic reader never creates, migrates or writes the thing it
            # is inspecting: there is nothing to apply and nothing to verify.
            if not self.sqlite_pragmas.read_only:
                self._apply_schema()
            self._initialized.set()

    # --- the two transaction shapes ---------------------------------------------

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        """Return read.

        A deferred transaction: several statements see one consistent snapshot.

        Yields:
            SQLite connection for the read transaction.

        Raises:
            RuntimeError: If the operation cannot continue.

        """
        self.initialize()
        connection = self._thread_connection()
        if connection.in_transaction:
            message = "nested SQLite repository transaction"
            raise RuntimeError(message)
        connection.execute("BEGIN")
        try:
            yield connection
        except BaseException:
            connection.rollback()
            raise
        else:
            connection.rollback()

    @contextmanager
    def write(self, *work_kinds: work_queue.WorkKind, notify_readers: bool = True) -> Iterator[sqlite3.Connection]:
        """`BEGIN IMMEDIATE`, then commit — or roll the whole thing back.

        Immediate rather than deferred because every writer here reads before it
        writes, and a deferred transaction would take the write lock only at the
        first write, leaving room for a racing peer to land in between.

        Yields:
            SQLite connection for the write transaction.

        Raises:
            RuntimeError: If the operation cannot continue.

        """
        self.initialize()
        connection = self._thread_connection()
        if connection.in_transaction:
            message = "nested SQLite repository transaction"
            raise RuntimeError(message)
        connection.execute("BEGIN IMMEDIATE")
        changes_before = connection.total_changes
        try:
            yield connection
        except BaseException:
            connection.rollback()
            raise
        _commit_write(connection)
        if notify_readers and self.changes is not None and connection.total_changes > changes_before:
            self.changes.publish()
        if self.work_queue is not None and connection.total_changes > changes_before:
            for work_kind in work_kinds:
                self.work_queue.put(work_kind)

    def _connect(self) -> sqlite3.Connection:
        if self.sqlite_pragmas.read_only:
            connection = sqlite3.connect(
                f"file:{self.path}?mode=ro",
                uri=True,
                timeout=self.sqlite_pragmas.timeout_seconds,
            )
        else:
            connection = sqlite3.connect(self.path, timeout=self.sqlite_pragmas.timeout_seconds)
        connection.row_factory = sqlite3.Row
        if self.sqlite_pragmas.foreign_keys:
            connection.execute("PRAGMA foreign_keys=ON")
        busy_timeout = self.sqlite_pragmas.busy_timeout_milliseconds
        connection.execute(f"PRAGMA busy_timeout={busy_timeout}")
        return connection

    def _thread_connection(self) -> sqlite3.Connection:
        connection: sqlite3.Connection | None = getattr(self._thread, "connection", None)
        if connection is None:
            connection = self._connect()
            self._thread.connection = connection
        return connection
