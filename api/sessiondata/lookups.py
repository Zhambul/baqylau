# Copyright (c) 2026 Zhambyl Yermagambet
"""Resolve session-data route values."""

from __future__ import annotations

from typing import TYPE_CHECKING

from domain.errors import UnknownReferenceError
from domain.lifecycle import LifecycleState

if TYPE_CHECKING:
    from core.repository import RepositoryQueries
    from domain.ids import SessionId
    from domain.session_state import SessionData
    from repository.contract.session_data import SessionDataRepository
    from repository.contract.sessions import SessionRepository
    from terminal.adapter import TerminalAdapter


def found(
    session_data_repository: SessionDataRepository,
    session_id: SessionId,
) -> SessionData:
    """Return a stored session aggregate.

    Returns:
        The session aggregate.

    Raises:
        UnknownReferenceError: If the session does not exist.

    """
    session_record = session_data_repository.read(session_id)
    if session_record is None:
        message = f"unknown session: {session_id}"
        raise UnknownReferenceError(message)
    return session_record


def is_live(terminal_adapter: TerminalAdapter, session_data: SessionData) -> bool:
    """Return true when the running session owns a terminal window.

    It uses the last terminal read, so a snapshot does not wait on a busy
    terminal. A pane gave up on a snapshot that waited, and its retries added
    more load.

    Returns:
        True when the session is live.

    """
    return (
        session_data.session.state == LifecycleState.RUNNING
        and terminal_adapter.shown_window(session_data.session.session_id) is not None
    )


def project_directory(
    session_repository: SessionRepository,
    repository_queries: RepositoryQueries,
    session_id: SessionId,
    working_directory: str,
) -> str:
    """Return the stable project directory for a session.

    Returns:
        The project directory.

    """
    session = session_repository.find(session_id)
    if session is not None and session.project_directory:
        return session.project_directory
    return repository_queries.project_directory(working_directory)
