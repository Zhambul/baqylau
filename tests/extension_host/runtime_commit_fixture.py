# Copyright (c) 2026 Zhambyl Yermagambet
"""Connect a real lifecycle repository to the production registry commit boundary."""

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event

from extensions.models.lifecycle_selection import RuntimeSelection
from extensions.registry import ActiveExtensionRegistry
from extensions.registry_contract import RegistryCommit
from extensions.registry_snapshot import RuntimeSnapshot, prepare_snapshot
from extensions.runtime_commit import StoredRegistryCommit
from repository.impl.sqlite.connection import SqliteDatabase
from repository.impl.sqlite.extension_lifecycle import SqliteExtensionLifecycleRepository
from tests.extension_host import lifecycle_fixture as lifecycle

INITIAL = "initial"


@dataclass(frozen=True)
class CommitCase:
    """Keep accepted state and the exact proposed registry selection together."""

    store: SqliteExtensionLifecycleRepository
    registry: ActiveExtensionRegistry
    snapshot: RuntimeSnapshot
    commit: StoredRegistryCommit


def accepted(directory: Path) -> CommitCase:
    """Prepare an empty runtime without claiming worker activation evidence.

    Returns:
        A real pending operation and its matching commit adapter.

    """
    store = lifecycle.claimed_repository(directory)
    operation = store.accept_extension_operation(lifecycle.proposal(store), lifecycle.NOW).operation
    assert operation is not None
    snapshot = prepare_snapshot(
        operation.proposal.candidate.catalog_revision, operation.proposal.candidate.runtime_revision, (),
    )
    return CommitCase(
        store, ActiveExtensionRegistry(INITIAL), snapshot, StoredRegistryCommit(store, operation, lifecycle.NOW + 1),
    )


def require_initial(registry: ActiveExtensionRegistry) -> None:
    """Read the real registry after a rejected or failed commit attempt."""
    with registry.read_snapshot() as selected:
        assert selected.revision == 0 and selected.snapshot.directory.runtime_revision == INITIAL


class FailingCommitConnection(sqlite3.Connection):
    """Fail one COMMIT after SQL writes have reached the actual connection."""

    fail_next_commit: bool

    def commit(self) -> None:
        """Inject one transaction failure, then permit a retry.

        Raises:
            sqlite3.OperationalError: When the test arms its next COMMIT.

        """
        if self.fail_next_commit:
            self.fail_next_commit = False
            message = "injected COMMIT failure"
            raise sqlite3.OperationalError(message)
        super().commit()


def fault_connection(database: SqliteDatabase) -> FailingCommitConnection:
    """Open a separate real connection with a single armed COMMIT failure.

    Returns:
        The injected connection, owned and closed by the test.

    """
    connection = sqlite3.connect(database.path, factory=FailingCommitConnection)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.fail_next_commit = True
    return connection


@dataclass(frozen=True)
class HeldCommit(RegistryCommit):
    """Hold the registry mutex after the real database accepts the runtime."""

    commit: StoredRegistryCommit
    committed: Event = field(default_factory=Event)
    released: Event = field(default_factory=Event)
    read_started: Event = field(default_factory=Event)

    def commit_registry(self, selection: RuntimeSelection) -> bool:
        """Pause only in this test adapter, after the actual durable commit.

        Returns:
            The real repository outcome after the test releases publication.

        """
        accepted = self.commit.commit_registry(selection)
        self.committed.set()
        assert self.released.wait(5)
        return accepted

    def read_after_commit(self, registry: ActiveExtensionRegistry) -> RuntimeSelection:
        """Try a fresh read while the exclusive publication boundary is held.

        Returns:
            The runtime visible when the registry finally admits this reader.

        """
        assert self.committed.wait(5)
        self.read_started.set()
        with registry.read_snapshot() as selected:
            return selected.snapshot.runtime_selection()
