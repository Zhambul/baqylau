# Copyright (c) 2026 Zhambyl Yermagambet
"""Decode the raw store's strict core and extension branches."""

import sqlite3
from typing import NamedTuple

from baqylau_extension_api.models.documents import EncodedDocument
from baqylau_extension_api.models.observations import ObservationCandidate
from baqylau_extension_api.models.source_results import PositionedObservation

from domain.ids import RawEventId
from extensions.models.observation_identity import observation_id
from extensions.models.observations import ExtensionObservation, ExtensionObservationMetadata, StoredObservation
from repository.impl.sqlite import rows
from repository.mapper import facts, raw_payloads


class ObservationValues(NamedTuple):
    """Name all stored extension values in SQL column order."""

    raw_event_id: RawEventId
    source_type: str
    source_identity: str
    source_name: str
    source_position: str
    observed_at: float
    payload: bytes
    payload_codec: str
    extension_metadata: str

    def sql_row(self) -> tuple[str | float | bytes, ...]:
        """Bind each named value explicitly at the SQL boundary.

        Returns:
            The complete parameter row in insertion order.

        """
        source_columns = (
            self.raw_event_id, self.source_type, self.source_identity, self.source_name, self.source_position,
        )
        content_columns = (
            self.observed_at, self.payload, self.payload_codec, self.extension_metadata,
        )
        return (*source_columns, *content_columns)


def observation_values(
    positioned: PositionedObservation, runtime_revision: str, observed_at: float,
) -> ObservationValues:
    """Keep exact document bytes separate from the typed metadata document.

    Returns:
        Complete parameters for one original extension input row.

    """
    candidate = positioned.observation
    stored, codec = raw_payloads.stored(candidate.document.json_text.encode("utf-8"))
    metadata = ExtensionObservationMetadata(
        observation_key=candidate.observation_key, scope=candidate.scope, schema_ref=candidate.document.schema_ref,
        occurred_at=candidate.occurred_at, causes=candidate.causes, runtime_revision=runtime_revision,
    )
    return ObservationValues(
        observation_id(candidate), candidate.source_type, candidate.source_identity, candidate.source_identity,
        positioned.position, observed_at, stored, codec, metadata.model_dump_json(),
    )


def stored_observation(row: sqlite3.Row) -> StoredObservation:
    """Select a closed branch before accessing any harness-only fields.

    Returns:
        The exact stored observation and its arrival cursor.

    """
    cursor = int(row["id"])
    if row["session_id"] is not None:
        return StoredObservation(cursor, facts.raw_event(rows.raw_event(row)))
    return StoredObservation(cursor, _extension_observation(row))


def _extension_observation(row: sqlite3.Row) -> ExtensionObservation:
    metadata = ExtensionObservationMetadata.model_validate_json(str(row["extension_metadata"]))
    payload = raw_payloads.restored(row["payload"], str(row["payload_codec"]))
    original = payload.decode("utf-8")
    return ExtensionObservation(
        raw_event_id=RawEventId(str(row["raw_event_id"])), observed_at=float(row["observed_at"]),
        runtime_revision=metadata.runtime_revision, source_position=str(row["source_position"]),
        candidate=ObservationCandidate(
            observation_key=metadata.observation_key, source_identity=str(row["source_identity"]),
            source_type=str(row["source_type"]), scope=metadata.scope,
            document=EncodedDocument(schema_ref=metadata.schema_ref, json_text=original),
            occurred_at=metadata.occurred_at, causes=metadata.causes,
        ),
    )
