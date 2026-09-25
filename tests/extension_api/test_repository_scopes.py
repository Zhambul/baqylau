# Copyright (c) 2026 Zhambyl Yermagambet
"""Linked worktrees share one repository ID, and each keeps its own Git directory (P10-T02)."""

from pathlib import Path

import pytest
from baqylau_extension_api.repositories import REPOSITORY_ARGUMENTS, repository_from_git

from core.repository import RepositoryQueries
from tests.canonical_sessiondata_api_git import create_linked_worktree


def git_repository(directory: Path) -> str:
    """Run Git with the SDK's arguments in one directory.

    Returns:
        Git's output.

    """
    completed = RepositoryQueries.run_git(str(directory), *REPOSITORY_ARGUMENTS)
    assert completed is not None
    return completed.stdout


def test_linked_worktrees_share_the_repository_id(tmp_path: Path) -> None:
    """The main worktree and a linked worktree have one ID and their own Git directories."""
    main, linked = tmp_path / "main", tmp_path / "linked"
    create_linked_worktree(main, linked)

    first = repository_from_git(git_repository(main))
    second = repository_from_git(git_repository(linked))

    assert first.repository_id == second.repository_id
    assert first.worktree == str(main.resolve())
    assert second.worktree == str(linked.resolve())
    assert first.git_directory != second.git_directory


def test_incomplete_output_is_refused() -> None:
    """Output without the three paths is not a repository."""
    with pytest.raises(ValueError, match="worktree and Git directories"):
        repository_from_git("/work\n")
