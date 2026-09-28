# Copyright (c) 2026 Zhambyl Yermagambet
"""Ask the host for the sessions whose working directory is in one repository."""

from typing import Annotated

from pydantic import Field

from baqylau_extension_api.models.base import WireModel
from baqylau_extension_api.models.scopes import RepositoryScope, SessionScope

MAX_REPOSITORY_SESSIONS = 200


class RepositorySessionsRequest(WireModel):
    """Name one repository."""

    scope: RepositoryScope


class RepositorySessionsReply(WireModel):
    """Give the newest sessions of the repository, newest first; `more` counts the older ones that are left out."""

    sessions: Annotated[tuple[SessionScope, ...], Field(max_length=MAX_REPOSITORY_SESSIONS)] = ()
    more: Annotated[int, Field(ge=0)] = 0
