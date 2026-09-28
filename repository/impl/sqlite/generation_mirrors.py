# Copyright (c) 2026 Zhambyl Yermagambet
"""Compare, archive, and restore the live and candidate rows of one owner's projection generations."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from repository.contract.projection_generations import GenerationComparison, GenerationState, ProjectionSwitchError
from repository.impl.sqlite import generation_heads, read_model_views

if TYPE_CHECKING:
    import sqlite3

_BEHIND_SQL = (
    "SELECT COUNT(*) AS behind FROM extension_projection_cursors AS live "
    "LEFT JOIN extension_projection_cursors AS candidate ON candidate.owner=live.owner "
    "AND candidate.scope=live.scope AND candidate.history_revision=live.history_revision "
    "AND candidate.generation=? "
    "WHERE live.owner=? AND live.generation=? AND COALESCE(candidate.commit_cursor, 0) < live.commit_cursor"
)


def require_caught_up(connection_handle: sqlite3.Connection, owner: str, generations: tuple[str, str]) -> None:
    """Require that the target generation read every scope as far as the live generation.

    Raises:
        ProjectionSwitchError: If the target is behind the live generation in one scope.

    """
    target, live = generations
    behind = connection_handle.execute(_BEHIND_SQL, (target, owner, live)).fetchone()
    if int(behind["behind"]):
        message = "the projection generation is behind the live generation"
        raise ProjectionSwitchError(message)


def ensure_generation(connection_handle: sqlite3.Connection, owner: str, history_revision: str) -> str:
    """Register the implicit default generation before its first switch.

    Returns:
        The owner's live generation.

    """
    live = generation_heads.active_generation(connection_handle, owner)
    if live == generation_heads.DEFAULT_GENERATION:
        live = f"{generation_heads.DEFAULT_GENERATION}:{owner}"
        now = time.time()
        connection_handle.execute(
            "INSERT OR IGNORE INTO extension_projection_generations(generation, owner, history_revision, state, "
            "created_at, updated_at) VALUES(?, ?, ?, 'active', ?, ?)",
            (live, owner, history_revision, now, now),
        )
        connection_handle.execute(
            "UPDATE extension_projection_cursors SET generation=? WHERE owner=? AND generation=?",
            (live, owner, generation_heads.DEFAULT_GENERATION),
        )
    return live


def make_live(connection_handle: sqlite3.Connection, owner: str, generations: tuple[str, str]) -> None:
    """Archive the previous live rows, restore the target's rows, and move the owner's head, in one transaction."""
    target, previous = generations
    archive_live(connection_handle, owner, previous)
    restore_candidate(connection_handle, owner, target)
    read_model_views.bump(connection_handle)
    now = time.time()
    connection_handle.execute(
        "INSERT INTO extension_projection_heads(owner, generation, switched_at) VALUES(?, ?, ?) "
        "ON CONFLICT(owner) DO UPDATE SET generation=excluded.generation, switched_at=excluded.switched_at",
        (owner, target, now),
    )
    connection_handle.execute(
        "UPDATE extension_projection_generations SET state=?, updated_at=? WHERE generation=?",
        (GenerationState.RETIRED, now, previous),
    )
    connection_handle.execute(
        "UPDATE extension_projection_generations SET state=?, updated_at=? WHERE generation=?",
        (GenerationState.ACTIVE, now, target),
    )


def archive_live(connection_handle: sqlite3.Connection, owner: str, generation: str) -> None:
    """Keep the live records and feed rows of an owner as the mirror rows of its previous generation."""
    connection_handle.execute(
        "INSERT OR REPLACE INTO extension_candidate_records(generation, owner, collection, record_key, scope, state, "
        "revision, schema_ref, document, summary) SELECT ?, owner, collection, record_key, scope, state, revision, "
        "schema_ref, document, summary FROM extension_records WHERE owner=?",
        (generation, owner),
    )
    connection_handle.execute("DELETE FROM extension_records WHERE owner=?", (owner,))
    connection_handle.execute(
        "INSERT OR REPLACE INTO extension_candidate_entries(generation, owner, commit_cursor, position, entry_id, "
        "session_id, entry_type, actor_id, parent_actor_id, turn_id, occurred_at, summary, payload) "
        "SELECT ?, ?, commit_cursor, position, entry_id, session_id, entry_type, actor_id, parent_actor_id, "
        "turn_id, occurred_at, summary, payload FROM session_entries "
        "WHERE entry_type='extension' AND json_extract(payload, '$.owner')=?",
        (generation, owner, owner),
    )
    connection_handle.execute(
        "DELETE FROM session_entries WHERE entry_type='extension' AND json_extract(payload, '$.owner')=?", (owner,),
    )


def restore_candidate(connection_handle: sqlite3.Connection, owner: str, generation: str) -> None:
    """Move the mirror rows of the switched generation into the live tables."""
    connection_handle.execute(
        "INSERT INTO extension_records(owner, collection, record_key, scope, state, revision, projection_revision, "
        "schema_ref, document, summary) SELECT owner, collection, record_key, scope, state, revision, generation, "
        "schema_ref, document, summary FROM extension_candidate_records WHERE generation=? AND owner=?",
        (generation, owner),
    )
    connection_handle.execute(
        "INSERT OR IGNORE INTO session_entries(commit_cursor, position, entry_id, session_id, entry_type, actor_id, "
        "parent_actor_id, turn_id, occurred_at, summary, payload) SELECT commit_cursor, position, entry_id, "
        "session_id, entry_type, actor_id, parent_actor_id, turn_id, occurred_at, summary, payload "
        "FROM extension_candidate_entries WHERE generation=? AND owner=? ORDER BY commit_cursor, position",
        (generation, owner),
    )
    connection_handle.execute(
        "DELETE FROM extension_candidate_records WHERE generation=? AND owner=?", (generation, owner),
    )
    connection_handle.execute(
        "DELETE FROM extension_candidate_entries WHERE generation=? AND owner=?", (generation, owner),
    )


def compare(connection_handle: sqlite3.Connection, owner: str, generation: str) -> GenerationComparison:
    """Count the live, candidate, and equal records and feed rows of one owner.

    Returns:
        The comparison.

    """
    record_rows = connection_handle.execute(
        "SELECT (SELECT COUNT(*) FROM extension_records WHERE owner=? AND state='stored') AS live, "
        "(SELECT COUNT(*) FROM extension_candidate_records WHERE generation=? AND owner=? AND state='stored') "
        "AS candidate, (SELECT COUNT(*) FROM extension_records AS live JOIN extension_candidate_records AS cand "
        "ON cand.generation=? AND cand.owner=live.owner AND cand.collection=live.collection AND cand.scope=live.scope "
        "AND cand.record_key=live.record_key WHERE live.owner=? AND live.state='stored' AND cand.state='stored' "
        "AND cand.document IS live.document AND cand.summary IS live.summary) AS equal",
        (owner, generation, owner, generation, owner),
    ).fetchone()
    entry_rows = connection_handle.execute(
        "SELECT (SELECT COUNT(*) FROM session_entries "
        "WHERE entry_type='extension' AND json_extract(payload, '$.owner')=?) AS live, "
        "(SELECT COUNT(*) FROM extension_candidate_entries WHERE generation=? AND owner=?) AS candidate, "
        "(SELECT COUNT(*) FROM session_entries AS live JOIN extension_candidate_entries AS cand ON cand.generation=? "
        "AND cand.entry_id=live.entry_id AND cand.payload=live.payload AND cand.summary IS live.summary "
        "WHERE live.entry_type='extension' AND json_extract(live.payload, '$.owner')=?) AS equal",
        (owner, generation, owner, generation, owner),
    ).fetchone()
    return GenerationComparison(
        live_records=int(record_rows["live"]), candidate_records=int(record_rows["candidate"]),
        equal_records=int(record_rows["equal"]), live_entries=int(entry_rows["live"]),
        candidate_entries=int(entry_rows["candidate"]), equal_entries=int(entry_rows["equal"]),
    )
