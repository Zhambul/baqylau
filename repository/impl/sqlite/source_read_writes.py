# Copyright (c) 2026 Zhambyl Yermagambet
"""Commit originals and source progress together, without worker calls inside SQL."""

import sqlite3

from extensions.models.observations import ObservationAppend
from extensions.models.source_reads import SourceCheckpoint, SourceReadCommit, SourceReadOutcome, next_checkpoint
from repository.impl.sqlite import observation_writes, source_read_acceptance, source_read_codec as codec


def commit_read(connection: sqlite3.Connection, request: SourceReadCommit) -> SourceReadOutcome:
    """Compare complete captured state and retain exact first results on retry.

    Returns:
        Accepted original rows and the checkpoint for this exact call.

    Raises:
        ValueError: If a call ID is reused or captured progress is stale.

    """
    proposal = request.proposal
    source_read_acceptance.validate_source_read(connection, proposal)
    binding = proposal.request.context.binding
    existing = codec.read_call(connection, binding.runtime_revision, binding.call_id)
    if existing is not None:
        if existing.proposal != proposal:
            message = "source call ID reused with a changed proposal"
            raise ValueError(message)
        return SourceReadOutcome(
            next_checkpoint(existing.proposal), codec.repeated_observations(connection, existing.proposal),
            repeated=True,
        )
    if codec.read_checkpoint(connection, proposal.checkpoint.key) != proposal.checkpoint:
        message = "source checkpoint is stale"
        raise ValueError(message)
    return _commit_new(connection, request)


def _commit_new(connection: sqlite3.Connection, request: SourceReadCommit) -> SourceReadOutcome:
    proposal = request.proposal
    binding = proposal.request.context.binding
    connection.execute(
        "INSERT INTO extension_source_reads(runtime_revision, call_id, proposal, observed_at) VALUES(?, ?, ?, ?)",
        (binding.runtime_revision, binding.call_id, proposal.model_dump_json(), request.observed_at),
    )
    observations = observation_writes.append(connection, ObservationAppend(
        extension_id=binding.extension_id, manager_id=proposal.manager_id, runtime_revision=binding.runtime_revision,
        scope=binding.scope, observed_at=request.observed_at, observations=proposal.response.observations,
    ))
    checkpoint = next_checkpoint(proposal)
    if checkpoint != proposal.checkpoint:
        _write_checkpoint(connection, request, checkpoint)
    return SourceReadOutcome(checkpoint, observations)


def _write_checkpoint(
    connection: sqlite3.Connection, request: SourceReadCommit, checkpoint: SourceCheckpoint,
) -> None:
    binding = request.proposal.request.context.binding
    key = checkpoint.key
    connection.execute(
        "INSERT INTO extension_source_checkpoints("
        "extension_id, scope, source_identity, source_type, revision, position, runtime_revision, call_id) "
        "VALUES(?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(extension_id, scope, source_identity) DO UPDATE SET "
        "source_type=excluded.source_type, revision=excluded.revision, position=excluded.position, "
        "runtime_revision=excluded.runtime_revision, call_id=excluded.call_id",
        (
            key.extension_id, key.scope.model_dump_json(), key.source_identity, checkpoint.source_type,
            checkpoint.revision, checkpoint.position, binding.runtime_revision, binding.call_id,
        ),
    )
