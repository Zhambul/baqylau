# Copyright (c) 2026 Zhambyl Yermagambet
"""Name a repository scope the same way in the host and in every package.

The ID is a digest of the repository's shared Git directory, so linked
worktrees of one repository share it. The host and packages run Git in their
own way, then read its output with `repository_from_git`.
"""

import hashlib
from pathlib import Path

from baqylau_extension_api.models.scopes import RepositoryScope

REPOSITORY_ID_LENGTH = 32
# Git prints the worktree, the worktree's Git directory, and the shared Git directory, one on each line.
REPOSITORY_ARGUMENTS = ("rev-parse", "--path-format=absolute", "--show-toplevel", "--git-dir", "--git-common-dir")
REPOSITORY_LINES = 3


def repository_id(common_directory: str) -> str:
    """Name a repository by its shared Git directory.

    Returns:
        The ID.

    """
    resolved = str(Path(common_directory).resolve())
    return hashlib.sha256(resolved.encode("utf-8")).hexdigest()[:REPOSITORY_ID_LENGTH]


def repository_from_git(output: str) -> RepositoryScope:
    """Read the scope from the output of Git with `REPOSITORY_ARGUMENTS`.

    Returns:
        The repository scope of the worktree.

    Raises:
        ValueError: If the output does not have the three paths.

    """
    lines = output.splitlines()
    if len(lines) < REPOSITORY_LINES:
        message = "Git did not print the worktree and Git directories"
        raise ValueError(message)
    worktree, git_directory, common_directory = lines[:REPOSITORY_LINES]
    return RepositoryScope(
        repository_id=repository_id(common_directory), worktree=worktree, git_directory=git_directory,
    )
