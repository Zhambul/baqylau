# Copyright (c) 2026 Zhambyl Yermagambet
"""Make one candidate history a closed session's default history in the caller's transaction."""

from __future__ import annotations

import time
from itertools import groupby
from operator import attrgetter
from typing import TYPE_CHECKING
from uuid import uuid4

from repository.contract.session_data import SessionDataChanges
from repository.impl.sqlite import history_checks, read_model_views, session_data_progress, session_data_write

if TYPE_CHECKING:
    import sqlite3

    from domain.ids import SessionId
    from repository.contract.folded_session import FoldedSession
    from repository.contract.history_reprocessing import HistoryReprocessing

DEFAULT_HISTORY = history_checks.DEFAULT_HISTORY
_MOVE_FACTS_SQL = (
    "UPDATE canonical_events SET history_revision=? WHERE history_revision=? "
    "AND (session_id=? OR json_extract(scope, '$.session_id')=?)"
)
# The interpretation tables name their session through the raw input.
_MOVE_INTERPRETATION_SQL = tuple(
    f"UPDATE {table} SET history_revision=? WHERE history_revision=? AND raw_event_id IN "  # noqa: S608 -- Fixed tables.
    "(SELECT raw_event_id FROM raw_events WHERE session_id=? OR json_extract(scope, '$.session_id')=?)"
    for table in (
        "interpretation_events", "interpretation_journal_steps", "interpretation_journal_bodies",
        "interpretation_journals", "interpretations",
    )
)
_MOVE_STATE_SQL = (
    "UPDATE extension_translation_state SET history_revision=? WHERE history_revision=? "
    "AND json_extract(scope, '$.session_id')=?"
)
_HEADS_SQL = (
    "INSERT INTO canonical_scope_heads(history_revision, scope, head_cursor) "
    "SELECT history_revision, scope, MAX(cursor) FROM canonical_events "
    "WHERE session_id=? OR json_extract(scope, '$.session_id')=? GROUP BY history_revision, scope"
)
_DELETE_READ_MODEL_SQL = (
    "DELETE FROM session_entries WHERE session_id=?",
    "DELETE FROM session_data_actors WHERE session_id=?",
    "DELETE FROM session_data WHERE session_id=?",
)
_LIFECYCLE_SQL = (
    "UPDATE sessions SET lifecycle = COALESCE((SELECT CASE event_type WHEN 'session.finished' THEN 'finished' "
    "ELSE 'running' END FROM current_canonical_events WHERE session_id=sessions.session_id AND origin='core' "
    "AND event_type IN ('session.started', 'session.finished') ORDER BY cursor DESC LIMIT 1), 'running') "
    "WHERE session_id=?"
)
_OBSERVERS_SQL = (
    "INSERT OR REPLACE INTO extension_observer_cursors(owner, scope, history_revision, generation, commit_cursor, "
    "updated_at) SELECT floors.owner, heads.scope, 'default', floors.generation, "
    "MAX(?, COALESCE(existing.commit_cursor, 0)), ? FROM extension_consumer_floors AS floors "
    "JOIN canonical_scope_heads AS heads ON heads.history_revision='default' "
    "AND json_extract(heads.scope, '$.session_id')=? "
    "LEFT JOIN extension_observer_cursors AS existing ON existing.owner=floors.owner "
    "AND existing.scope=heads.scope AND existing.history_revision='default' "
    "AND existing.generation=floors.generation "
    "WHERE floors.consumer='observer' AND floors.history_revision='default'"
)
_RECORD_STATES_SQL = (
    ("UPDATE history_reprocessings SET state='retired', updated_at=? WHERE session_id=? AND state='active'"),
    (
        "INSERT INTO history_reprocessings(history_revision, session_id, state, replay_cursor, live_head, "
        "created_at, updated_at) VALUES(?, ?, 'retired', 0, ?, ?, ?)"
    ),
    "UPDATE history_reprocessings SET state='active', updated_at=? WHERE history_revision=?",
)


def switch_history(
    connection_handle: sqlite3.Connection, history_reprocessing: HistoryReprocessing, folded_session: FoldedSession,
) -> None:
    """Move the default history to an archive, promote the candidate, and replace the session's read model.

    The caller's transaction must defer foreign keys. Every check runs
    before any change, so a refusal changes nothing.
    """
    session_id = history_reprocessing.session_id
    history_checks.require_switchable(connection_handle, session_id, history_reprocessing.live_head)
    archive = _archive_live(connection_handle, history_reprocessing)
    _replace_read_model(connection_handle, session_id, folded_session)
    head = history_checks.session_head(connection_handle, session_id, DEFAULT_HISTORY)
    _reset_consumers(connection_handle, session_id, head)
    progress = session_data_progress.read_progress(connection_handle)
    session_data_progress.write_progress(connection_handle, max(progress, head))
    read_model_views.bump(connection_handle)
    _record_states(connection_handle, history_reprocessing, archive, head)


def _archive_live(connection_handle: sqlite3.Connection, history_reprocessing: HistoryReprocessing) -> str:
    """Move the live facts to a new archive history and promote the candidate's facts.

    Returns:
        The archive history revision.

    """
    session_id = history_reprocessing.session_id
    archive = f"archive-{uuid4().hex}"
    connection_handle.execute(
        "INSERT INTO canonical_histories(history_revision, created_at) VALUES(?, ?)", (archive, time.time()),
    )
    _move_history(connection_handle, session_id, (DEFAULT_HISTORY, archive))
    _move_history(connection_handle, session_id, (history_reprocessing.history_revision, DEFAULT_HISTORY))
    connection_handle.execute(
        "DELETE FROM canonical_scope_heads WHERE json_extract(scope, '$.session_id')=?", (session_id,),
    )
    connection_handle.execute(_HEADS_SQL, (session_id, session_id))
    return archive


def _move_history(connection_handle: sqlite3.Connection, session_id: SessionId, move: tuple[str, str]) -> None:
    source, target = move
    move_values = (target, source, session_id, session_id)
    connection_handle.execute(_MOVE_FACTS_SQL, move_values)
    for statement in _MOVE_INTERPRETATION_SQL:
        connection_handle.execute(statement, move_values)
    connection_handle.execute(_MOVE_STATE_SQL, (target, source, session_id))


def _replace_read_model(
    connection_handle: sqlite3.Connection, session_id: SessionId, folded_session: FoldedSession,
) -> None:
    """Write the folded rows through the live writer; each commit cursor keeps its feed rows."""
    for statement in _DELETE_READ_MODEL_SQL:
        connection_handle.execute(statement, (session_id,))
    for commit_cursor, folded_entries in groupby(folded_session.entries, key=attrgetter("commit_cursor")):
        feed = SessionDataChanges(entries=tuple(folded.entry for folded in folded_entries))
        session_data_write.write_changes(connection_handle, session_id, feed, commit_cursor)
    facts = SessionDataChanges(session=folded_session.session, actors=folded_session.actors)
    session_data_write.write_changes(connection_handle, session_id, facts, folded_session.cursor)
    connection_handle.execute(_LIFECYCLE_SQL, (session_id,))


def _reset_consumers(connection_handle: sqlite3.Connection, session_id: SessionId, head: int) -> None:
    """Project the session again from its new facts; never observe replayed facts."""
    now = time.time()
    connection_handle.execute("DELETE FROM extension_records WHERE session_id=?", (session_id,))
    connection_handle.execute(
        "UPDATE extension_projection_cursors SET commit_cursor=0, updated_at=? "
        "WHERE json_extract(scope, '$.session_id')=?",
        (now, session_id),
    )
    connection_handle.execute(_OBSERVERS_SQL, (head, now, session_id))


def _record_states(
    connection_handle: sqlite3.Connection, history_reprocessing: HistoryReprocessing, archive: str, head: int,
) -> None:
    now = time.time()
    retire, keep, activate = _RECORD_STATES_SQL
    connection_handle.execute(retire, (now, history_reprocessing.session_id))
    connection_handle.execute(keep, (archive, history_reprocessing.session_id, head, now, now))
    connection_handle.execute(activate, (now, history_reprocessing.history_revision))
