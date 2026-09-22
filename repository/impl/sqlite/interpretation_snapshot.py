# Copyright (c) 2026 Zhambyl Yermagambet
"""Capture bounded scope state without fetching an unbounded page of fact bodies."""

import sqlite3

from baqylau_extension_api.models.canonical import CommittedFact, CoreStateSnapshot

from extensions.models.interpretation_snapshot import PriorStateRequest, SnapshotCapture
from repository.impl.sqlite import interpretation_codec, interpretation_selection


def capture_snapshot(connection: sqlite3.Connection, request: PriorStateRequest) -> CoreStateSnapshot:
    """Read metadata first and decode only bodies which can fit the byte limit.

    Returns:
        An ordered prefix and a checked completeness flag from one SQL snapshot.

    """
    _require_head(connection, request)
    captured = SnapshotCapture(request)
    rows = connection.execute(
        "SELECT cursor, length(CAST(payload AS BLOB)) + length(CAST(scope AS BLOB)) + "
        "COALESCE(length(CAST(extension_metadata AS BLOB)), 0) AS content_bytes "
        "FROM canonical_events WHERE history_revision=? AND scope=? AND cursor<=? ORDER BY cursor LIMIT ?",
        (
            request.history_revision, request.scope.model_dump_json(),
            request.expected_canonical_cursor, request.max_facts + 1,
        ),
    )
    for row in rows:
        if len(captured.facts) >= request.max_facts:
            return captured.finish(complete=False)
        if int(row["content_bytes"]) > request.max_bytes:
            return captured.finish(complete=False)
        if not captured.append(_fact(connection, int(row["cursor"]))):
            return captured.finish(complete=False)
    return captured.finish(complete=True)


def require_complete(
    connection: sqlite3.Connection, request: PriorStateRequest, snapshot: CoreStateSnapshot,
) -> None:
    """Check a complete claim after each supplied fact's identity and body have been checked.

    Raises:
        ValueError: If a complete snapshot omits a stored fact in its exact scope.

    """
    if not snapshot.complete:
        return
    row = connection.execute(
        "SELECT COUNT(*) AS fact_count FROM (SELECT 1 FROM canonical_events "
        "WHERE history_revision=? AND scope=? AND cursor<=? LIMIT ?)",
        (
            request.history_revision, request.scope.model_dump_json(),
            request.expected_canonical_cursor, len(snapshot.facts) + 1,
        ),
    ).fetchone()
    count = None if row is None else int(row["fact_count"])
    if count != len(snapshot.facts):
        message = "complete prior snapshot omits facts from its selected scope"
        raise ValueError(message)


def _require_head(connection: sqlite3.Connection, request: PriorStateRequest) -> None:
    history = connection.execute(
        "SELECT 1 FROM canonical_histories WHERE history_revision=?", (request.history_revision,),
    ).fetchone()
    if history is None:
        message = "prior snapshot history is not recorded"
        raise ValueError(message)
    head = interpretation_selection.canonical_head(connection, request.history_revision)
    if head != request.expected_canonical_cursor:
        message = "prior snapshot canonical boundary is stale"
        raise ValueError(message)


def _fact(connection: sqlite3.Connection, cursor: int) -> CommittedFact:
    row = connection.execute("SELECT * FROM canonical_events WHERE cursor=?", (cursor,)).fetchone()
    if row is None:
        message = "prior snapshot lost a fact inside its read transaction"
        raise RuntimeError(message)
    stored = interpretation_codec.stored_fact(row)
    return CommittedFact(fact=stored.fact, cursor=stored.cursor, accepted_at=stored.accepted_at)
