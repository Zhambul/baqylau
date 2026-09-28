# Copyright (c) 2026 Zhambyl Yermagambet
"""The host names a directory's repository with the SDK's rule, and no repository for other directories (P10-T02)."""

from pathlib import Path

from baqylau_extension_api.repositories import repository_id
from fastapi import Response

from api.extensions.repository_scope_routes import extension_repository_scope
from tests.canonical_sessiondata_api_git import create_linked_worktree


def test_directory_names_its_repository(tmp_path: Path) -> None:
    """A linked worktree resolves to itself; the ID follows the shared Git directory."""
    main, linked = tmp_path / "main", tmp_path / "linked"
    create_linked_worktree(main, linked)
    response = Response()

    found = extension_repository_scope(str(linked), response).scope

    assert found is not None
    assert found.worktree == str(linked.resolve())
    assert found.repository_id == repository_id(str(main / ".git"))
    assert response.headers["Cache-Control"] == "no-store"


def test_other_directories_have_no_repository(tmp_path: Path) -> None:
    """A plain or missing directory has no repository scope."""
    assert extension_repository_scope(str(tmp_path), Response()).scope is None
    assert extension_repository_scope(str(tmp_path / "missing"), Response()).scope is None
