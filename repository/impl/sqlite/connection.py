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

import os
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from threading import Event, Lock

from repository.errors import SchemaVersionMismatch


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
AUDIT_PRAGMAS = SqlitePragmas(busy_timeout_milliseconds=5_000, timeout_seconds=5.0)

READ_ONLY_PRAGMAS = SqlitePragmas(read_only=True, file_mode=None)


class SqliteDatabase:
    """One file, one schema, one policy. Initialised once per process.

    Every call opens a fresh short-lived connection: the daemon serves requests
    on many threads and sqlite connections are thread-bound.
    """

    def __init__(
        self,
        path: str,
        schema: str,
        schema_version: int,
        pragmas: SqlitePragmas = DEFAULT_PRAGMAS,
    ) -> None:
        self.path = os.path.abspath(path)
        self.schema = schema
        self.schema_version = schema_version
        self.pragmas = pragmas
        # An Event, not a bool: the fast path is a read the type checker
        # cannot narrow, which is exactly right — another thread may set it
        # between the two checks below, and that is the point of them.
        self._initialized = Event()
        self._initialize_lock = Lock()

    # --- opening ---------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        if self.pragmas.read_only:
            connection = sqlite3.connect(
                f"file:{self.path}?mode=ro",
                uri=True,
                timeout=self.pragmas.timeout_seconds,
            )
        else:
            connection = sqlite3.connect(self.path, timeout=self.pragmas.timeout_seconds)
        connection.row_factory = sqlite3.Row
        if self.pragmas.foreign_keys:
            connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(f"PRAGMA busy_timeout={self.pragmas.busy_timeout_milliseconds}")
        return connection

    def exists(self) -> bool:
        return os.path.isfile(self.path)

    def initialize(self) -> None:
        """Create or verify the file. Idempotent, and cheap after the first call."""
        if self._initialized.is_set():
            return
        with self._initialize_lock:
            if self._initialized.is_set():
                return          # a peer thread won the lock and did the work
            # A forensic reader never creates, migrates or writes the thing it
            # is inspecting: there is nothing to apply and nothing to verify.
            if not self.pragmas.read_only:
                self._apply_schema()
            self._initialized.set()

    def _apply_schema(self) -> None:
        os.makedirs(os.path.dirname(self.path), mode=0o700, exist_ok=True)
        connection = self._connect()
        try:
            with connection:
                if self.pragmas.journal_mode:
                    connection.execute(f"PRAGMA journal_mode={self.pragmas.journal_mode}")
                connection.executescript(self.schema)
                self._verify_version(connection)
        finally:
            connection.close()
        if self.pragmas.file_mode is not None:
            os.chmod(self.path, self.pragmas.file_mode)

    def _verify_version(self, connection: sqlite3.Connection) -> None:
        row = connection.execute("SELECT version FROM schema_version WHERE id=1").fetchone()
        if row is None:
            connection.execute(
                "INSERT INTO schema_version(id, version, applied_at) VALUES(1, ?, ?)",
                (self.schema_version, time.time()),
            )
            return
        if int(row["version"]) != self.schema_version:
            raise SchemaVersionMismatch(
                f"{os.path.basename(self.path)} was written by schema version "
                f"{row['version']}, this build expects {self.schema_version}"
            )

    # --- the two transaction shapes ---------------------------------------------

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        """A deferred transaction: several statements see one consistent snapshot."""
        self.initialize()
        connection = self._connect()
        try:
            connection.execute("BEGIN")
            yield connection
            connection.rollback()
        finally:
            connection.close()

    @contextmanager
    def write(self) -> Iterator[sqlite3.Connection]:
        """`BEGIN IMMEDIATE`, then commit — or roll the whole thing back.

        Immediate rather than deferred because every writer here reads before it
        writes, and a deferred transaction would take the write lock only at the
        first write, leaving room for a racing peer to land in between.
        """
        self.initialize()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
            except BaseException:
                connection.rollback()
                raise
            connection.commit()
        finally:
            connection.close()
