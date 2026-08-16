"""Git repository and worktree facts about a directory on this machine."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass


@dataclass(frozen=True)
class RepositoryStatus:
    branch: str
    worktree: str | None
    dirty: bool


class RepositoryQueries:
    """Resolve the stable project directory used to group sessions."""

    @staticmethod
    def canonical_directory(working_directory: str) -> str:
        return os.path.realpath(working_directory) if working_directory else ""

    def project_directory(self, working_directory: str) -> str:
        if not working_directory:
            return ""
        canonical_directory = self.canonical_directory(working_directory)
        current_directory = canonical_directory
        while os.path.isdir(current_directory):
            git_marker = os.path.join(current_directory, ".git")
            if os.path.isdir(git_marker):
                return canonical_directory
            if os.path.isfile(git_marker):
                with open(git_marker, encoding="utf-8", errors="replace") as marker:
                    marker_text = marker.readline().strip()
                if not marker_text.startswith("gitdir:"):
                    return canonical_directory
                git_directory = marker_text.removeprefix("gitdir:").strip()
                if not os.path.isabs(git_directory):
                    git_directory = os.path.normpath(
                        os.path.join(current_directory, git_directory)
                    )
                worktree_segment = os.sep + "worktrees" + os.sep
                if worktree_segment in git_directory:
                    return os.path.dirname(
                        os.path.dirname(os.path.dirname(git_directory))
                    )
                return canonical_directory
            parent_directory = os.path.dirname(current_directory)
            if parent_directory == current_directory:
                return canonical_directory
            current_directory = parent_directory
        return canonical_directory

    @staticmethod
    def run_git(working_directory: str, *arguments: str) -> subprocess.CompletedProcess | None:
        try:
            return subprocess.run(
                ["git", "-C", working_directory, *arguments],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None

    @classmethod
    def status(cls, working_directory: str) -> RepositoryStatus | None:
        if not working_directory:
            return None
        branch_result = cls.run_git(
            working_directory,
            "branch",
            "--show-current",
        )
        if branch_result is None or branch_result.returncode != 0:
            return None
        branch = branch_result.stdout.strip()
        if not branch:
            revision_result = cls.run_git(
                working_directory,
                "rev-parse",
                "--short",
                "HEAD",
            )
            if revision_result is None:
                return None
            branch = revision_result.stdout.strip() or "?"
        status_result = cls.run_git(
            working_directory,
            "status",
            "--porcelain",
        )
        if status_result is None:
            return None
        worktree = (
            os.path.basename(os.path.realpath(working_directory))
            if os.path.isfile(os.path.join(working_directory, ".git"))
            else None
        )
        return RepositoryStatus(branch, worktree, bool(status_result.stdout))
