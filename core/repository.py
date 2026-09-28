# Copyright (c) 2026 Zhambyl Yermagambet
"""Git repository and worktree facts about a directory on this machine."""

from __future__ import annotations

import shutil
import subprocess  # noqa: S404 -- Run a bounded local Git status query.
from pathlib import Path

from core.git_status import RepositoryStatus, parse_status

GIT_TIMEOUT_SECONDS = 2
# Every query here only reads. Without this option `git status` can take the index lock to refresh the
# index; the time limit then kills it and leaves `.git/index.lock`, so the user's next Git command fails.
READ_ONLY = "--no-optional-locks"
GIT_EXECUTABLE = shutil.which("git") or "git"


class RepositoryQueries:
    """Resolve the stable project directory used to group sessions."""

    git_executable = GIT_EXECUTABLE

    @classmethod
    def canonical_directory(cls, working_directory: str) -> str:
        """Return the resolved form of a working directory.

        Returns:
            Resolved form of a working directory.

        """
        if not working_directory:
            return ""
        return str(_canonical_path(working_directory))

    @classmethod
    def project_directory(cls, working_directory: str) -> str:
        """Return the main checkout directory for a working directory.

        Returns:
            Main checkout directory for a working directory.

        """
        if not working_directory:
            return ""
        canonical_path = _canonical_path(working_directory)
        git_marker = _find_git_marker(canonical_path)
        if git_marker is None or git_marker.is_dir():
            return str(canonical_path)
        project_path = _linked_project_path(git_marker)
        return str(canonical_path if project_path is None else project_path)

    @classmethod
    def run_git(cls, working_directory: str, *arguments: str) -> subprocess.CompletedProcess[str] | None:
        """Run one bounded Git query.

        Returns:
            The completed process.

        """
        try:
            return subprocess.run(  # noqa: S603 -- Use the configured Git executable with separate query arguments, without a shell.
                [cls.git_executable, READ_ONLY, "-C", working_directory, *arguments],
                capture_output=True,
                text=True,
                timeout=GIT_TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None

    @classmethod
    def status(cls, working_directory: str) -> RepositoryStatus | None:
        """Return the current Git status for a working directory.

        Returns:
            Current Git status for a working directory.

        """
        if not working_directory:
            return None
        status_result = cls.run_git(
            working_directory,
            "status",
            "--porcelain=v2",
            "--branch",
        )
        if status_result is None or status_result.returncode != 0:
            return None
        branch, dirty = parse_status(status_result.stdout)
        working_path = _canonical_path(working_directory)
        worktree = working_path.name if (working_path / ".git").is_file() else None
        return RepositoryStatus(branch, worktree, dirty)


def _canonical_path(working_directory: str) -> Path:
    return Path(working_directory).resolve()


def _find_git_marker(working_path: Path) -> Path | None:
    if not working_path.is_dir():
        return None
    for candidate_directory in (working_path, *working_path.parents):
        git_marker = candidate_directory / ".git"
        if git_marker.exists():
            return git_marker
    return None


def _linked_project_path(git_marker: Path) -> Path | None:
    marker_text = git_marker.open(encoding="utf-8", errors="replace").readline().strip()
    if not marker_text.startswith("gitdir:"):
        return None
    git_path = Path(marker_text.removeprefix("gitdir:").strip())
    if not git_path.is_absolute():
        git_path = (git_marker.parent / git_path).resolve()
    if git_path.parent.name == "worktrees":
        return git_path.parent.parent.parent
    return None
