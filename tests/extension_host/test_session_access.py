# Copyright (c) 2026 Zhambyl Yermagambet
"""A worker that declares `uses_sessions` lists the sessions of one repository."""

import subprocess  # noqa: S404 -- Make test repositories with the Git program.
from dataclasses import dataclass
from pathlib import Path

from baqylau_extension_api.models.scopes import RepositoryScope
from baqylau_extension_api.models.session_lists import RepositorySessionsRequest

from domain.ids import ActorId, HarnessName, SessionId
from extensions.repository_scopes import repository_of
from extensions.session_access import HostSessionDirectory
from repository.contract.session_rows import SessionRow

GIT_SECONDS = 30


@dataclass(frozen=True)
class FixedRows:
    """Give fixed session rows, newest first."""

    rows: tuple[SessionRow, ...]

    def session_rows(self) -> tuple[SessionRow, ...]:
        """Give the rows.

        Returns:
            The rows.

        """
        return self.rows


def repository(directory: Path) -> RepositoryScope:
    """Make a Git repository and name it by the SDK's rule.

    Returns:
        The repository scope.

    """
    directory.mkdir()
    command = ("git", "init", "-q", str(directory))
    subprocess.run(command, check=True, timeout=GIT_SECONDS)  # noqa: S603 -- A fixed Git command.
    scope = repository_of(str(directory))
    assert scope is not None
    return scope


def row(session_id: str, directory: Path) -> SessionRow:
    """Name one session in a directory.

    Returns:
        The row.

    """
    return SessionRow(
        session_id=SessionId(session_id), lead_actor_id=ActorId(f"{session_id}-actor"), harness=HarnessName("codex"),
        working_directory=str(directory),
    )


def test_only_the_repository_sessions_are_listed(tmp_path: Path) -> None:
    """A session in a subdirectory is in the repository; one in another repository or no repository is not."""
    first = tmp_path / "first"
    scope = repository(first)
    repository(tmp_path / "second")
    (first / "src").mkdir()
    rows = FixedRows((
        row("newest", first / "src"),
        row("other", tmp_path / "second"),
        row("outside", tmp_path),
        row("oldest", first),
    ))

    reply = HostSessionDirectory(rows).repository_sessions(RepositorySessionsRequest(scope=scope))

    assert [(scope.session_id, scope.actor_id) for scope in reply.sessions] == [
        ("newest", "newest-actor"), ("oldest", "oldest-actor"),
    ]
    assert reply.more == 0
