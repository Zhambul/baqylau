# Copyright (c) 2026 Zhambyl Yermagambet
"""Read source progress and complete accepted calls without loading feature code."""

import sqlite3

from baqylau_extension_api.models.base import Identifier

from extensions.models.observation_identity import observation_id
from extensions.models.observations import ObservationAppendOutcome
from extensions.models.source_reads import SourceCheckpoint, SourceKey, SourceReadCommit, SourceReadProposal
from repository.impl.sqlite import observation_codec


def read_checkpoint(connection: sqlite3.Connection, key: SourceKey) -> SourceCheckpoint:
    """Select the complete owner, scope, and source identity.

    Returns:
        Committed progress, or revision zero for a new source.

    """
    row = connection.execute(
        "SELECT source_type, revision, position FROM extension_source_checkpoints "
        "WHERE extension_id=? AND scope=? AND source_identity=?",
        (key.extension_id, key.scope.model_dump_json(), key.source_identity),
    ).fetchone()
    if row is None:
        return SourceCheckpoint(key=key)
    return SourceCheckpoint(
        key=key, revision=int(row["revision"]),
        source_type=str(row["source_type"]), position=str(row["position"]),
    )


def read_call(
    connection: sqlite3.Connection, runtime_revision: Identifier, call_id: Identifier,
) -> SourceReadCommit | None:
    """Read the original request, reply, and first host observation time.

    Returns:
        A complete accepted call, or no record for an unknown call.

    """
    row = connection.execute(
        "SELECT proposal, observed_at FROM extension_source_reads WHERE runtime_revision=? AND call_id=?",
        (runtime_revision, call_id),
    ).fetchone()
    if row is None:
        return None
    return SourceReadCommit(
        proposal=SourceReadProposal.model_validate_json(row["proposal"]), observed_at=float(row["observed_at"]),
    )


def repeated_observations(connection: sqlite3.Connection, proposal: SourceReadProposal) -> ObservationAppendOutcome:
    """Read the first original rows without writing or adding pending work.

    Returns:
        No new rows and all original rows from the accepted call.

    Raises:
        RuntimeError: If an accepted call has lost an original row.

    """
    repeated = []
    for positioned in proposal.response.observations:
        row = connection.execute(
            "SELECT * FROM raw_events WHERE raw_event_id=?", (observation_id(positioned.observation),),
        ).fetchone()
        if row is None:
            message = "accepted source call has no original observation"
            raise RuntimeError(message)
        repeated.append(observation_codec.stored_observation(row))
    return ObservationAppendOutcome(accepted=(), repeated=tuple(repeated))
