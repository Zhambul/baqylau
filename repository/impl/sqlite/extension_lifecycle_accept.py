# Copyright (c) 2026 Zhambyl Yermagambet
"""Reserve one immutable candidate and user intent before worker preparation."""

import sqlite3

from extensions.models.lifecycle_operations import LifecycleOperation, LifecycleProposal
from extensions.models.lifecycle_state import LifecycleAdmission, LifecycleState
from repository.impl.sqlite import (
    extension_lifecycle_reads as reads,
    extension_lifecycle_validation as validation,
    extension_lifecycle_writes as writes,
    extension_settings_validation as settings,
)


def accept_operation(
    connection: sqlite3.Connection, proposal: LifecycleProposal, created_at: float,
) -> LifecycleAdmission:
    """Keep admission, runtime reservation, and requested state in one transaction.

    Returns:
        Accepted work or an explicit replay, stale head, or busy operation.

    """
    current = reads.read_state(connection)
    existing = reads.read_operation(connection, proposal.operation_id)
    if existing is not None:
        return _replay(current, existing, proposal)
    if _is_stale(connection, current, proposal):
        return LifecycleAdmission(status="stale", state=current)
    if current.pending_operation is not None:
        return LifecycleAdmission(status="busy", state=current)
    _validate_selection(connection, current, proposal)
    operation = LifecycleOperation(
        proposal=proposal, accepted_revision=current.revision + 1, status="preparing",
        created_at=created_at, updated_at=created_at,
    )
    writes.insert_operation(connection, operation)
    writes.write_intents(connection, proposal.intents)
    writes.write_head(connection, writes.next_head(current, pending_operation=proposal.operation_id))
    return LifecycleAdmission(status="accepted", state=reads.read_state(connection), operation=operation)


def _is_stale(connection: sqlite3.Connection, lifecycle_state: LifecycleState, proposal: LifecycleProposal) -> bool:
    catalog = connection.execute("SELECT revision FROM extension_catalog_head WHERE id=1").fetchone()
    revision = 0 if catalog is None else int(catalog["revision"])
    return (
        lifecycle_state.revision != proposal.expected_revision or lifecycle_state.manager_id != proposal.manager_id
        or revision != proposal.candidate.catalog_revision
        or not settings.settings_are_current(lifecycle_state, proposal)
    )


def _validate_selection(
    connection: sqlite3.Connection, lifecycle_state: LifecycleState, proposal: LifecycleProposal,
) -> None:
    if reads.read_runtime(connection, proposal.candidate.runtime_revision) is not None:
        message = "lifecycle operation requires an unused runtime revision"
        raise ValueError(message)
    validation.validate_proposal(connection, lifecycle_state, proposal)
    settings.validate_changes(connection, proposal)


def _replay(
    lifecycle_state: LifecycleState, existing: LifecycleOperation, proposal: LifecycleProposal,
) -> LifecycleAdmission:
    if existing.proposal != proposal:
        message = "operation ID already belongs to a different lifecycle request"
        raise ValueError(message)
    return LifecycleAdmission(status="replayed", state=lifecycle_state, operation=existing)
