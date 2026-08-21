# api/sessiondata/routes.py — the whole read surface: the list, one aggregate,
# one page of the feed.
#
# Each of these is an indexed read of the read model and nothing else. The
# canonical log is not opened here, no fold happens here, and there is no cache
# in front of any of it — the fold happened once, when the fact arrived.
from __future__ import annotations

from fastapi import APIRouter, Query

from api.common.models.fields import SessionIdPath
from api.sessiondata import mapper
from api.sessiondata.models.entry import EntryPageResponse
from api.sessiondata.models.session_data import SessionDataResponse
from app.providers import Repositories, SessionDataStore, Terminal
from core.repository import RepositoryStatus
from domain.errors import UnknownReference
from domain.ids import SessionId
from domain.sessiondata import SessionData
from repository.contract.session_data import SessionDataRepository
from terminal.adapter import TerminalAdapter

router = APIRouter()

DEFAULT_ENTRY_LIMIT = 200
MAXIMUM_ENTRY_LIMIT = 1000


@router.get("/sessionData")
def session_data_list(
    read_model: SessionDataStore,
    terminal: Terminal,
    repositories: Repositories,
) -> tuple[SessionDataResponse, ...]:
    """Every visible session's aggregate — the list view, in two queries.

    The two read-time lookups are batched because both are subprocesses: git is
    asked once per DIRECTORY, and the terminal is asked for its window list
    ONCE — a machine with twenty sessions in four checkouts would otherwise pay
    for sixteen git answers it already had and twenty window listings for one.
    """
    known: dict[str, RepositoryStatus | None] = {}

    def repository(working_directory: str) -> RepositoryStatus | None:
        if working_directory not in known:
            known[working_directory] = repositories.status(working_directory)
        return known[working_directory]

    visible = read_model.visible()
    live = terminal.live_sessions(data.session.session_id for data in visible)
    return tuple(
        mapper.session_data(
            data,
            live=data.session.session_id in live,
            repository=repository(data.session.working_directory),
        )
        for data in visible
    )


@router.get("/sessionData/{session_id}")
def session_data(
    session_id: SessionIdPath,
    read_model: SessionDataStore,
    terminal: Terminal,
    repositories: Repositories,
) -> SessionDataResponse:
    data = _found(read_model, SessionId(session_id))
    return mapper.session_data(
        data,
        live=_live(terminal, data.session.session_id),
        repository=repositories.status(data.session.working_directory),
    )


@router.get("/sessionData/{session_id}/entries")
def session_entries(
    session_id: SessionIdPath,
    read_model: SessionDataStore,
    at: int | None = None,
    before: int | None = None,
    limit: int = Query(DEFAULT_ENTRY_LIMIT, ge=1, le=MAXIMUM_ENTRY_LIMIT),
) -> EntryPageResponse:
    """One page of the feed, oldest first.

    `at` is the snapshot's cursor: the page is read AS OF it, so the page and the
    snapshot describe one instant and the stream opened from the same cursor
    picks up exactly where the page stops. `before` pages further back.
    """
    return mapper.entry_page(
        read_model.entries_page(SessionId(session_id), at=at, before=before, limit=limit)
    )


def _found(read_model: SessionDataRepository, session_id: SessionId) -> SessionData:
    data = read_model.read(session_id)
    if data is None:
        # By type, not a bare KeyError: this is the caller naming a session that
        # does not exist, and it is the reason the 400 handler exists at all.
        raise UnknownReference(f"unknown session: {session_id}")
    return data


def _live(terminal: TerminalAdapter, session_id: SessionId) -> bool:
    """Whether a terminal window is attached right now.

    The window id itself is never exposed — a frontend needs to know whether the
    session is attended, not the handle it is attended through.
    """
    return terminal.window_for_session(session_id) is not None
