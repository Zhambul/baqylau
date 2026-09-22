# Copyright (c) 2026 Zhambyl Yermagambet
"""Append checked extension input within one repository-owned transaction."""

import sqlite3

from baqylau_extension_api.models.source_results import PositionedObservation

from extensions.models.observations import (
    ExtensionObservation,
    ObservationAppend,
    ObservationAppendOutcome,
    StoredObservation,
)
from repository.errors import EventIdentityConflictError
from repository.impl.sqlite import observation_codec as codec, observation_validation as validation

_INSERT = (
    "INSERT INTO raw_events(raw_event_id, source_type, source_identity, source_name, source_position, "
    "observed_at, payload, payload_codec, extension_metadata, encoding) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, 'json')"
)


def append(connection: sqlite3.Connection, request: ObservationAppend) -> ObservationAppendOutcome:
    """Store originals and pending work, retaining exact first rows on repetition.

    Returns:
        New and repeated rows with their original host metadata.

    """
    accepted = []
    repeated = []
    for positioned in request.observations:
        insert_row = codec.observation_values(positioned, request.runtime_revision, request.observed_at)
        existing = connection.execute(
            "SELECT * FROM raw_events WHERE raw_event_id=?", (insert_row.raw_event_id,),
        ).fetchone()
        if existing is None:
            accepted.append(_append_one(connection, positioned, insert_row))
        else:
            _require_same(existing, positioned)
            repeated.append(codec.stored_observation(existing))
    return ObservationAppendOutcome(tuple(accepted), tuple(repeated))


def _append_one(
    connection: sqlite3.Connection, positioned: PositionedObservation, insert_row: codec.ObservationValues,
) -> StoredObservation:
    validation.validate_causes(connection, positioned.observation.causes)
    connection.execute(_INSERT, insert_row.sql_row())
    row = connection.execute("SELECT * FROM raw_events WHERE raw_event_id=?", (insert_row.raw_event_id,)).fetchone()
    connection.execute(
        "INSERT INTO pending_raw_events(raw_event_row_id, raw_event_id) VALUES(?, ?)",
        (row["id"], insert_row.raw_event_id),
    )
    return codec.stored_observation(row)


def _require_same(row: sqlite3.Row, positioned: PositionedObservation) -> None:
    stored = codec.stored_observation(row).observation
    if not isinstance(stored, ExtensionObservation) or (
        stored.candidate != positioned.observation or stored.source_position != positioned.position
    ):
        message = f"raw event identity reused: {row['raw_event_id']}"
        raise EventIdentityConflictError(message)
