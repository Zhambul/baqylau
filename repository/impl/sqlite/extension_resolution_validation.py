# Copyright (c) 2026 Zhambyl Yermagambet
"""Recheck complete converted settings before changing any accepted head."""

import sqlite3

from extensions.models.lifecycle_operations import LifecycleCompletion, LifecycleProposal
from extensions.models.lifecycle_state import LifecycleState
from extensions.models.runtime_candidates import MigratingRuntimeSelection
from repository.impl.sqlite import (
    extension_lifecycle_validation as validation,
    extension_settings_validation as settings,
)


def resolved_proposal(
    connection: sqlite3.Connection, lifecycle_state: LifecycleState,
    proposal: LifecycleProposal, completion: LifecycleCompletion,
) -> LifecycleProposal:
    """Validate the permitted conversion and retain all unaffected state exactly.

    Returns:
        Complete checked settings for the commit; the stored request stays immutable.

    Raises:
        ValueError: If a migration is missing, substituted, or stale.

    """
    resolution = completion.resolution
    if not isinstance(proposal.candidate, MigratingRuntimeSelection):
        if resolution is not None:
            message = "a complete runtime cannot accept an unrequested migration result"
            raise ValueError(message)
        return proposal
    if resolution is None:
        message = "a successful migration requires its complete resolved settings"
        raise ValueError(message)
    resolution.validate_candidate(proposal.candidate)
    validation.validate_proposal(connection, lifecycle_state, proposal)
    resolved = LifecycleProposal(
        operation_id=proposal.operation_id, manager_id=proposal.manager_id,
        expected_revision=proposal.expected_revision, kind=proposal.kind, candidate=resolution.runtime,
        intents=proposal.intents, request_origin=proposal.request_origin,
        settings_changes=(*proposal.settings_changes, *resolution.settings_changes),
    )
    if not settings.settings_are_current(lifecycle_state, resolved):
        message = "migration source settings revision changed before completion"
        raise ValueError(message)
    validation.validate_proposal(connection, lifecycle_state, resolved)
    settings.validate_changes(connection, resolved)
    return resolved
