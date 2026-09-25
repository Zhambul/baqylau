# Copyright (c) 2026 Zhambyl Yermagambet
"""Name the repository scope of a directory, so a page can show a repository with no session."""

from typing import Annotated

from fastapi import APIRouter, Query, Response

from api.extensions.web_models import RepositoryScopeResponse
from extensions.repository_scopes import repository_of

router = APIRouter()


@router.get("/api/extension-web/repository-scope")
def extension_repository_scope(
    directory: Annotated[str, Query(min_length=1)], response: Response,
) -> RepositoryScopeResponse:
    """Resolve the directory with Git.

    Returns:
        The scope; none for a missing directory or a directory with no repository.

    """
    response.headers["Cache-Control"] = "no-store"
    return RepositoryScopeResponse(scope=repository_of(directory))
