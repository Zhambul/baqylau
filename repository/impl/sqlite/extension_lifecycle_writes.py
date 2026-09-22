# Copyright (c) 2026 Zhambyl Yermagambet
"""Write lifecycle rows only inside the repository's existing transaction."""

import sqlite3
from dataclasses import dataclass

from baqylau_extension_api.models.base import Identifier

from extensions.models.lifecycle_operations import LifecycleOperation
from extensions.models.lifecycle_selection import ExtensionIntent, SettingsChange
from extensions.models.lifecycle_state import LifecycleState


@dataclass(frozen=True)
class LifecycleHead:
    """Name the complete stored head independently of the public state aggregate."""

    revision: int
    manager_id: Identifier | None
    committed_runtime: str | None
    pending_operation: str | None


def write_head(connection: sqlite3.Connection, lifecycle_head: LifecycleHead) -> None:
    """Replace the head after all referenced operation and runtime rows exist."""
    connection.execute(
        "INSERT INTO extension_lifecycle_head(id, revision, manager_id, committed_runtime, pending_operation) "
        "VALUES(1, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET revision=excluded.revision, "
        "manager_id=excluded.manager_id, committed_runtime=excluded.committed_runtime, "
        "pending_operation=excluded.pending_operation",
        (
            lifecycle_head.revision, lifecycle_head.manager_id,
            lifecycle_head.committed_runtime, lifecycle_head.pending_operation,
        ),
    )


def insert_operation(connection: sqlite3.Connection, operation: LifecycleOperation) -> None:
    """Reserve the runtime identity and operation together before preparation."""
    candidate = operation.proposal.candidate
    connection.execute(
        "INSERT INTO extension_runtime_revisions(runtime_revision, selection) VALUES(?, ?)",
        (candidate.runtime_revision, candidate.model_dump_json()),
    )
    connection.execute(
        "INSERT INTO extension_lifecycle_operations("
        "operation_id, manager_id, runtime_revision, accepted_revision, status, created_at, updated_at, proposal"
        ") VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
        (
            operation.proposal.operation_id, operation.proposal.manager_id, candidate.runtime_revision,
            operation.accepted_revision, operation.status, operation.created_at, operation.updated_at,
            operation.proposal.model_dump_json(),
        ),
    )


def write_outcome(connection: sqlite3.Connection, operation: LifecycleOperation) -> None:
    """Keep immutable proposal bytes while changing only the operation outcome."""
    connection.execute(
        "UPDATE extension_lifecycle_operations SET status=?, updated_at=?, failure=? WHERE operation_id=?",
        (
            operation.status, operation.updated_at,
            None if operation.failure is None else operation.failure.model_dump_json(), operation.proposal.operation_id,
        ),
    )


def write_intents(connection: sqlite3.Connection, intents: tuple[ExtensionIntent, ...]) -> None:
    """Retain explicit user choices independently of preparation success."""
    connection.executemany(
        "INSERT INTO extension_requests(extension_id, enabled, package_digest) VALUES(?, ?, ?) "
        "ON CONFLICT(extension_id) DO UPDATE SET enabled=excluded.enabled, package_digest=excluded.package_digest",
        tuple((intent.extension_id, int(intent.enabled), intent.package_digest) for intent in intents),
    )


def write_settings(connection: sqlite3.Connection, changes: tuple[SettingsChange, ...]) -> None:
    """Accept complete raw overrides only after successful runtime preparation."""
    connection.executemany(
        "INSERT INTO extension_settings(extension_id, overrides) VALUES(?, ?) "
        "ON CONFLICT(extension_id) DO UPDATE SET overrides=excluded.overrides",
        tuple((change.extension_id, change.settings.model_dump_json()) for change in changes),
    )


def next_head(
    lifecycle_state: LifecycleState, *, manager_id: Identifier | None = None,
    pending_operation: str | None = None, committed_runtime: str | None = None,
) -> LifecycleHead:
    """Advance one management revision and preserve the last committed set by default.

    Returns:
        A named SQL head, with no untyped update map.

    """
    previous = None if lifecycle_state.committed_runtime is None else lifecycle_state.committed_runtime.runtime_revision
    return LifecycleHead(
        lifecycle_state.revision + 1, manager_id or lifecycle_state.manager_id, committed_runtime or previous,
        pending_operation,
    )
