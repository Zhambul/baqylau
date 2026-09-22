# Copyright (c) 2026 Zhambyl Yermagambet
"""Use the existing core codec and one explicit extension metadata model."""

import sqlite3
from dataclasses import astuple

from baqylau_extension_api.models.canonical import CanonicalFact, CoreFact
from baqylau_extension_api.models.documents import EncodedDocument
from baqylau_extension_api.models.events import ExtensionFact

from extensions.mapper.core_events import private_candidate, public_candidate
from extensions.models.interpretations import ExtensionFactMetadata, StoredCanonicalFact
from repository.impl.sqlite import rows
from repository.mapper import facts as mapper

CORE_COLUMNS = (
    "event_id, schema_version, event_type, session_id, actor_id, turn_id, parent_actor_id, harness, "
    "occurred_at, terminal_window_id, harness_process_id, accepted_at, payload"
)


def stored_fact(row: sqlite3.Row) -> StoredCanonicalFact:
    """Decode the correct branch before reading its fields.

    Source links remain in the interpretation journal and link table. This
    accepted-body read does not add later observations to the core fact.

    Returns:
        The accepted body with its host-owned cursor, time, and history.

    """
    fact = (
        _extension_fact(row) if row["session_id"] is None
        else public_candidate(mapper.row_canonical_event(rows.canonical_event(row)))
    )
    return StoredCanonicalFact(
        fact=fact, cursor=int(row["cursor"]), accepted_at=float(row["accepted_at"]),
        history_revision=str(row["history_revision"]),
    )


def _extension_fact(row: sqlite3.Row) -> ExtensionFact:
    metadata = ExtensionFactMetadata.model_validate_json(str(row["extension_metadata"]))
    document = EncodedDocument(schema_ref=metadata.schema_ref, json_text=str(row["payload"]))
    return ExtensionFact(
        event_id=str(row["event_id"]), event_type=str(row["event_type"]),
        scope=metadata.scope,
        occurred_at=row["occurred_at"], causes=metadata.causes,
        document=document,
    )


def insert_fact(connection: sqlite3.Connection, fact: CanonicalFact, history_revision: str, accepted_at: float) -> None:
    """Write one checked branch without allocating identity or time in extension code."""
    if isinstance(fact, CoreFact):
        insert_row = astuple(mapper.canonical_event_insert_row(private_candidate(fact), accepted_at))
        connection.execute(
            f"INSERT INTO canonical_events({CORE_COLUMNS}, history_revision) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (*insert_row, history_revision),
        )
    else:
        metadata = ExtensionFactMetadata(scope=fact.scope, schema_ref=fact.document.schema_ref, causes=fact.causes)
        connection.execute(
            "INSERT INTO canonical_events(event_id, event_type, occurred_at, accepted_at, payload, "
            "history_revision, extension_metadata) VALUES(?, ?, ?, ?, ?, ?, ?)",
            (fact.event_id, fact.event_type, fact.occurred_at, accepted_at, fact.document.json_text,
             history_revision, metadata.model_dump_json()),
        )
