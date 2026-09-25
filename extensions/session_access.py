# Copyright (c) 2026 Zhambyl Yermagambet
"""List the sessions of a repository for a worker that declares `uses_sessions`.

Each distinct working directory is resolved to its repository once for each
call, by the SDK's repository rule, so the host and every package agree.
"""

import functools
from dataclasses import dataclass

from baqylau_extension_api.contracts.session_lists import ExtensionSessionDirectory
from baqylau_extension_api.models.scopes import SessionScope
from baqylau_extension_api.models.session_lists import (
    MAX_REPOSITORY_SESSIONS,
    RepositorySessionsReply,
    RepositorySessionsRequest,
)

from extensions.repository_scopes import repository_of
from repository.contract.session_rows import SessionRow, SessionRows


@dataclass(frozen=True)
class HostSessionDirectory(ExtensionSessionDirectory):
    """Read the stored sessions and keep those in the named repository."""

    rows: SessionRows

    def repository_sessions(self, sessions_request: RepositorySessionsRequest) -> RepositorySessionsReply:
        """List the repository's sessions, newest first.

        Returns:
            The newest sessions, and the count of older ones that are left out.

        """
        wanted = RepositorySessionsRequest.model_validate(sessions_request).scope.repository_id
        # Each distinct directory runs Git once for this call.
        repository_id = functools.cache(_repository_id)
        rows = self.rows.session_rows()
        found = [_scope(row) for row in rows if repository_id(row.working_directory) == wanted]
        more = max(0, len(found) - MAX_REPOSITORY_SESSIONS)
        return RepositorySessionsReply(sessions=tuple(found[:MAX_REPOSITORY_SESSIONS]), more=more)


def _repository_id(directory: str) -> str | None:
    scope = repository_of(directory)
    return None if scope is None else scope.repository_id


def _scope(row: SessionRow) -> SessionScope:
    return SessionScope(session_id=row.session_id, actor_id=row.lead_actor_id, harness=row.harness)
