# Copyright (c) 2026 Zhambyl Yermagambet
"""Fence an old manager only after the new daemon has exclusive process ownership."""

import sqlite3

from extensions.models.lifecycle_operations import LifecycleFailure, LifecycleOperation
from extensions.models.lifecycle_state import LifecycleWrite, ManagerClaim
from repository.impl.sqlite import extension_lifecycle_reads as reads, extension_lifecycle_writes as writes


def claim_manager(connection: sqlite3.Connection, claim: ManagerClaim) -> LifecycleWrite:
    """Interrupt pending preparation without changing the last committed runtime.

    Returns:
        The new manager head or current state after a stale claim.

    """
    current = reads.read_state(connection)
    if current.revision != claim.expected_revision:
        return LifecycleWrite(accepted=False, state=current)
    if current.manager_id == claim.manager_id:
        return LifecycleWrite(accepted=True, state=current)
    pending = reads.read_operation(connection, current.pending_operation)
    if pending is not None:
        _interrupt(connection, pending, claim.claimed_at)
    writes.write_head(connection, writes.next_head(current, manager_id=claim.manager_id))
    return LifecycleWrite(accepted=True, state=reads.read_state(connection))


def _interrupt(connection: sqlite3.Connection, pending: LifecycleOperation, claimed_at: float) -> None:
    writes.write_outcome(connection, LifecycleOperation(
        proposal=pending.proposal, accepted_revision=pending.accepted_revision, status="interrupted",
        created_at=pending.created_at, updated_at=max(claimed_at, pending.updated_at), failure=LifecycleFailure(
            code="interrupted", detail="the previous manager stopped before this operation completed",
        ),
    ))
