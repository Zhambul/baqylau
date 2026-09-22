# Copyright (c) 2026 Zhambyl Yermagambet
"""Commit accepted runtime state or retain the old set after preparation failure."""

import sqlite3

from extensions.models.lifecycle_operations import LifecycleCompletion, LifecycleOperation
from extensions.models.lifecycle_state import LifecycleState, LifecycleWrite
from repository.impl.sqlite import (
    extension_lifecycle_reads as reads,
    extension_lifecycle_writes as writes,
    extension_resolution_validation as validation,
)


def finish_operation(connection: sqlite3.Connection, completion: LifecycleCompletion) -> LifecycleWrite:
    """Fence late managers and commit only the candidate already reserved in storage.

    Returns:
        A committed outcome, an idempotent prior outcome, or the unchanged stale state.

    """
    current = reads.read_state(connection)
    operation = reads.read_operation(connection, completion.operation_id)
    if operation is None or current.manager_id != completion.manager_id:
        return LifecycleWrite(accepted=False, state=current)
    if current.revision == completion.expected_revision + 1 and _same_completed(operation, completion):
        return LifecycleWrite(accepted=True, state=current)
    if not _can_finish(current, operation, completion):
        return LifecycleWrite(accepted=False, state=current)
    _commit_outcome(connection, current, operation, completion)
    return LifecycleWrite(accepted=True, state=reads.read_state(connection))


def _same_completed(operation: LifecycleOperation, completion: LifecycleCompletion) -> bool:
    if operation.resolution != completion.resolution:
        return False
    status = "succeeded" if completion.failure is None else "failed"
    return (
        operation.status == status and operation.failure == completion.failure
        and operation.proposal.manager_id == completion.manager_id
        and operation.accepted_revision == completion.expected_revision
    )


def _can_finish(
    lifecycle_state: LifecycleState, operation: LifecycleOperation, completion: LifecycleCompletion,
) -> bool:
    return (
        lifecycle_state.revision == completion.expected_revision
        and lifecycle_state.pending_operation == completion.operation_id
        and operation.status == "preparing" and operation.proposal.manager_id == completion.manager_id
    )


def _commit_outcome(
    connection: sqlite3.Connection, lifecycle_state: LifecycleState,
    operation: LifecycleOperation, completion: LifecycleCompletion,
) -> None:
    finished = LifecycleOperation(
        proposal=operation.proposal, accepted_revision=operation.accepted_revision,
        status="succeeded" if completion.failure is None else "failed", created_at=operation.created_at,
        updated_at=max(completion.completed_at, operation.updated_at), failure=completion.failure,
        resolution=completion.resolution,
    )
    writes.write_outcome(connection, finished)
    committed = None
    if finished.status == "succeeded":
        resolved = validation.resolved_proposal(connection, lifecycle_state, operation.proposal, completion)
        committed = resolved.candidate.runtime_revision
        writes.write_settings(connection, resolved.settings_changes)
        if completion.resolution is not None:
            connection.execute(
                "INSERT INTO extension_runtime_resolutions(runtime_revision, resolution) VALUES(?, ?)",
                (committed, completion.resolution.model_dump_json()),
            )
        connection.execute(
            "UPDATE extension_runtime_revisions SET committed_at=? WHERE runtime_revision=?",
            (finished.updated_at, committed),
        )
    writes.write_head(connection, writes.next_head(lifecycle_state, committed_runtime=committed))
