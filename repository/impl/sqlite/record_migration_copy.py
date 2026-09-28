# Copyright (c) 2026 Zhambyl Yermagambet
"""Copy one owner's live projection rows into a migrating generation, replace its converted rows, or discard it."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from baqylau_extension_api.models.documents import SchemaRef

from extensions.models.record_migration import RecordGeneration, StoredRecordSchema
from repository.contract.projection_generations import GenerationState
from repository.impl.sqlite import generation_heads, generation_mirrors

if TYPE_CHECKING:
    import sqlite3

    from baqylau_extension_api.models.record_changes import PutRecord

_RECORDS_SQL = (
    "INSERT INTO extension_candidate_records(generation, owner, collection, record_key, scope, state, revision, "
    "schema_ref, document, summary) SELECT ?, owner, collection, record_key, scope, state, revision, "
    "schema_ref, document, summary FROM extension_records WHERE owner=?"
)
_ENTRIES_SQL = (
    "INSERT INTO extension_candidate_entries(generation, owner, commit_cursor, position, entry_id, session_id, "
    "entry_type, actor_id, parent_actor_id, turn_id, occurred_at, summary, payload) SELECT ?, ?, "
    "commit_cursor, position, entry_id, session_id, entry_type, actor_id, parent_actor_id, turn_id, occurred_at, "
    "summary, payload FROM session_entries WHERE entry_type='extension' AND json_extract(payload, '$.owner')=?"
)
# The cursor and the floor statements take the generation, the time, the owner, and the live generation.
_POSITION_SQL = (
    (
        "INSERT INTO extension_projection_cursors(owner, scope, history_revision, generation, commit_cursor, "
        "updated_at) SELECT owner, scope, history_revision, ?, commit_cursor, ? "
        "FROM extension_projection_cursors WHERE owner=? AND generation=?"
    ),
    (
        "INSERT INTO extension_consumer_floors(consumer, owner, history_revision, generation, floor_cursor, "
        "created_at) SELECT consumer, owner, history_revision, ?, floor_cursor, ? "
        "FROM extension_consumer_floors WHERE consumer='projection' AND owner=? AND generation=?"
    ),
)
_REPLACE_SQL = (
    "UPDATE extension_candidate_records SET schema_ref=?, document=?, summary=? WHERE generation=? AND owner=? "
    "AND collection=? AND scope=? AND record_key=? AND state='stored' AND revision=?"
)
_DISCARD_SQL = (
    "DELETE FROM extension_candidate_records WHERE generation=?",
    "DELETE FROM extension_candidate_entries WHERE generation=?",
    "DELETE FROM extension_projection_cursors WHERE generation=?",
    "DELETE FROM extension_consumer_floors WHERE generation=?",
)


def copy_live(connection_handle: sqlite3.Connection, owner: str, generation: str) -> None:
    """Create the migrating generation and copy the owner's live rows, feed rows, cursors, and floor into it.

    The copied cursors let the new projector continue after the snapshot
    once the generation becomes live.
    """
    now = time.time()
    connection_handle.execute(
        "INSERT INTO extension_projection_generations(generation, owner, history_revision, state, created_at, "
        "updated_at) VALUES(?, ?, 'default', ?, ?, ?)",
        (generation, owner, GenerationState.MIGRATING, now, now),
    )
    connection_handle.execute(_RECORDS_SQL, (generation, owner))
    connection_handle.execute(_ENTRIES_SQL, (generation, owner, owner))
    live = generation_heads.active_generation(connection_handle, owner)
    for statement in _POSITION_SQL:
        connection_handle.execute(statement, (generation, now, owner, live))


def stored_schemas(connection_handle: sqlite3.Connection) -> tuple[StoredRecordSchema, ...]:
    """Read each distinct owner, collection, and schema of the live stored rows.

    Returns:
        The stored schemas in owner and collection order.

    """
    rows = connection_handle.execute(
        "SELECT DISTINCT owner, collection, schema_ref FROM extension_records WHERE state='stored' "
        "ORDER BY owner, collection, schema_ref",
    ).fetchall()
    return tuple(StoredRecordSchema(
        extension_id=row["owner"], collection=row["collection"],
        schema_ref=SchemaRef.model_validate_json(row["schema_ref"]),
    ) for row in rows)


def replace_row(connection_handle: sqlite3.Connection, generation: str, put_record: PutRecord) -> None:
    """Replace one converted row at its captured revision.

    Raises:
        ValueError: If the row changed after it was captured.

    """
    key = put_record.key
    document = put_record.document
    changed = connection_handle.execute(_REPLACE_SQL, (
        document.schema_ref.model_dump_json(), document.json_text, put_record.summary,
        generation, key.owner, key.collection, key.scope.model_dump_json(), key.key, put_record.expected_revision,
    )).rowcount
    if changed != 1:
        message = "a migrating record changed after it was captured"
        raise ValueError(message)


def discard(connection_handle: sqlite3.Connection, generation: str) -> None:
    """Fail one migrating generation and delete its copied rows; a live or finished generation is not changed."""
    changed = connection_handle.execute(
        "UPDATE extension_projection_generations SET state=?, updated_at=? WHERE generation=? AND state=?",
        (GenerationState.FAILED, time.time(), generation, GenerationState.MIGRATING),
    ).rowcount
    if changed:
        for statement in _DISCARD_SQL:
            connection_handle.execute(statement, (generation,))


def promote(connection_handle: sqlite3.Connection, record_generation: RecordGeneration) -> None:
    """Make one migrating generation its owner's live generation in the lifecycle commit transaction.

    The previous live generation stays retired with its rows.

    Raises:
        ValueError: If the generation is not a migrating generation of that owner.

    """
    row = connection_handle.execute(
        "SELECT owner, state FROM extension_projection_generations WHERE generation=?",
        (record_generation.generation,),
    ).fetchone()
    migrating = row is not None and row["state"] == GenerationState.MIGRATING
    if not migrating or row["owner"] != record_generation.extension_id:
        message = "the converted records are not a migrating generation of this extension"
        raise ValueError(message)
    owner = record_generation.extension_id
    previous = generation_mirrors.ensure_generation(connection_handle, owner, "default")
    generation_mirrors.make_live(connection_handle, owner, (record_generation.generation, previous))


def discard_interrupted(connection_handle: sqlite3.Connection) -> None:
    """Fail every migrating generation that a stopped daemon left behind."""
    rows = connection_handle.execute(
        "SELECT generation FROM extension_projection_generations WHERE state=?", (GenerationState.MIGRATING,),
    ).fetchall()
    for row in rows:
        discard(connection_handle, row["generation"])
