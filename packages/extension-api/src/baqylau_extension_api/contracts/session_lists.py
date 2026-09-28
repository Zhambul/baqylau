# Copyright (c) 2026 Zhambyl Yermagambet
"""List the sessions of a repository, so a repository view can read session-scoped peer data."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from baqylau_extension_api.models.session_lists import RepositorySessionsReply, RepositorySessionsRequest


class ExtensionSessionDirectory(Protocol):
    """Name the sessions whose working directory is in a repository, by the SDK's repository rule."""

    def repository_sessions(self, sessions_request: RepositorySessionsRequest) -> RepositorySessionsReply:
        """List the repository's sessions."""
        ...
