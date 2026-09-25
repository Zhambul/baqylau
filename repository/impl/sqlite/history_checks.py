# Copyright (c) 2026 Zhambyl Yermagambet
"""Check the safe V1 boundary of one closed session inside the caller's transaction."""

from __future__ import annotations

from typing import TYPE_CHECKING, NoReturn

from repository.contract.history_reprocessing import ReprocessingRefusedError
from repository.impl.sqlite import session_data_progress

if TYPE_CHECKING:
    import sqlite3

    from domain.ids import SessionId

DEFAULT_HISTORY = "default"
_SESSION_HEAD_SQL = (
    "SELECT COALESCE(MAX(cursor), 0) FROM canonical_events WHERE history_revision=? "
    "AND (session_id=? OR json_extract(scope, '$.session_id')=?)"
)
_PENDING_SQL = (
    "SELECT 1 FROM pending_raw_events AS pending JOIN raw_events AS raw ON raw.id=pending.raw_event_row_id "
    "WHERE raw.session_id=? OR json_extract(raw.scope, '$.session_id')=? LIMIT 1"
)
_JOBS_SQL = "SELECT 1 FROM extension_jobs WHERE session_id=? AND state IN ('accepted', 'running') LIMIT 1"
_UNREAD_SQL = "SELECT 1 FROM canonical_events WHERE history_revision='default' AND cursor>? LIMIT 1"


def refuse(message: str) -> NoReturn:
    """Refuse an operation outside the safe V1 boundary.

    Raises:
        ReprocessingRefusedError: Always, with the reason.

    """
    raise ReprocessingRefusedError(message)


def require_closed(connection_handle: sqlite3.Connection, session_id: SessionId) -> None:
    """Require a finished session with no pending input and no running jobs."""
    session = connection_handle.execute("SELECT lifecycle FROM sessions WHERE session_id=?", (session_id,)).fetchone()
    if session is None or session["lifecycle"] != "finished":
        refuse("only a finished session can be reprocessed")
    if connection_handle.execute(_PENDING_SQL, (session_id, session_id)).fetchone() is not None:
        refuse("the session has input that is not interpreted yet")
    if connection_handle.execute(_JOBS_SQL, (session_id,)).fetchone() is not None:
        refuse("the session has extension jobs that did not finish")


def require_switchable(connection_handle: sqlite3.Connection, session_id: SessionId, live_head: int) -> None:
    """Require a closed session whose live history did not change after the request, and a drained core consumer."""
    require_closed(connection_handle, session_id)
    if session_head(connection_handle, session_id, DEFAULT_HISTORY) != live_head:
        refuse("the session's live history changed after the switch was requested")
    require_drained(connection_handle)


def require_drained(connection_handle: sqlite3.Connection) -> None:
    """Require that the core consumer has read every live fact."""
    progress = session_data_progress.read_progress(connection_handle)
    if connection_handle.execute(_UNREAD_SQL, (progress,)).fetchone() is not None:
        refuse("the core consumer has live facts that it did not read yet")


def session_head(connection_handle: sqlite3.Connection, session_id: SessionId, history_revision: str) -> int:
    """Read the last cursor of one session in one history.

    Returns:
        The cursor, or zero when the history has no fact of the session.

    """
    row = connection_handle.execute(_SESSION_HEAD_SQL, (history_revision, session_id, session_id)).fetchone()
    return int(row[0])
