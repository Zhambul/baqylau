# Copyright (c) 2026 Zhambyl Yermagambet
"""Serve the session-data list and directory history."""

from fastapi import APIRouter

from api.sessiondata import lookups, mapper
from api.sessiondata.models.session_data import SessionDataListResponse
from app.provider_harness_sessions import Sessions
from app.provider_runtime import Repositories
from app.provider_session_storage import SessionDataStore
from app.provider_terminal import Terminal
from core.git_status import RepositoryStatus

router = APIRouter()


class _RepositoryStatusCache:
    def __init__(self, repositories: Repositories) -> None:
        self.repositories = repositories
        self.known: list[tuple[str, RepositoryStatus | None]] = []

    def status(self, working_directory: str) -> RepositoryStatus | None:
        """Return one cached repository status.

        Returns:
            The repository status.

        """
        for known_directory, cached_status in self.known:
            if known_directory == working_directory:
                return cached_status
        repository_status = self.repositories.status(working_directory)
        self.known.append((working_directory, repository_status))
        return repository_status


@router.get("/sessionData")
def session_data_list(
    read_model: SessionDataStore,
    terminal: Terminal,
    repositories: Repositories,
    session_storage: Sessions,
) -> SessionDataListResponse:
    """Return the live session-data list.

    Returns:
        The session-data list.

    """
    cursor = read_model.high_water_cursor()
    repository_statuses = _RepositoryStatusCache(repositories)
    visible = read_model.running()
    live = terminal.shown_sessions(session_record.session.session_id for session_record in visible)
    return SessionDataListResponse(
        cursor=cursor,
        sessions=tuple(
            mapper.session_data(
                session_record,
                live=True,
                repository_status=repository_statuses.status(
                    session_record.session.working_directory,
                ),
                project_directory=lookups.project_directory(
                    session_storage,
                    repositories,
                    session_record.session.session_id,
                    session_record.session.working_directory,
                ),
            )
            for session_record in visible
            if session_record.session.session_id in live
        ),
    )


@router.get("/sessionData/directories")
def session_directories(read_model: SessionDataStore) -> tuple[str, ...]:
    """Return session directories in recent-use order.

    Returns:
        The session directories.

    """
    return read_model.working_directories()
