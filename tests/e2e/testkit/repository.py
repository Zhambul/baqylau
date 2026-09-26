# Copyright (c) 2026 Zhambyl Yermagambet
"""An isolated real Git worktree for repository-state cases."""

from __future__ import annotations

import fcntl
import json
import os
import stat
import tempfile
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from tests.e2e.testkit import git_commands

if TYPE_CHECKING:
    from collections.abc import Iterator

TEXT_ENCODING = "utf-8"
HOOKS_DIRECTORY_NAME = "hooks"


@dataclass(frozen=True)
class RepositoryWorkspace:
    """Represent repository workspace."""

    working_directory: str
    repository_root: str
    branch: str
    worktree: str

    @classmethod
    def create(cls, root: Path) -> RepositoryWorkspace:
        """Create a committed Git repository and a linked test worktree.

        Returns:
            The repository and worktree paths with the test branch name.

        """
        source = root / "source-repository"
        linked = root / "e2e-linked-worktree"
        source.mkdir()
        git_commands.run(source, "init", "--initial-branch=e2e-main")
        git_commands.run(source, "config", "user.name", "Baqylau E2E")
        git_commands.run(source, "config", "user.email", "baqylau-e2e@example.invalid")
        (source / "repository-state.txt").write_text("CLEAN_REPOSITORY_STATE\n")
        git_commands.run(source, "add", "repository-state.txt")
        git_commands.run(source, "commit", "-m", "Create repository state fixture")
        git_commands.run(source, "worktree", "add", "-b", "e2e-worktree", str(linked))
        return cls(str(linked), str(source), "e2e-worktree", linked.name)

    def trust_for_codex(self, codex_home: Path) -> None:
        """Process trust for codex."""
        with (codex_home / "config.toml").open("a", encoding=TEXT_ENCODING) as config:
            config.write(
                f'\n[projects.{json.dumps(self.repository_root)}]\ntrust_level = "trusted"\n',
            )

    def trust_for_claude_code(
        self,
        state_file: Path,
    ) -> tuple[ClaudeCodeProjectTrust, ...]:
        """Grant temporary Claude trust to the repository and linked worktree.

        Returns:
            The trust records that restore the previous state when closed.

        """
        granted: list[ClaudeCodeProjectTrust] = []
        try:
            granted.extend(
                ClaudeCodeProjectTrust.grant(state_file, directory)
                for directory in (self.working_directory, self.repository_root)
            )
        except BaseException:
            for trust in reversed(granted):
                trust.close()
            raise
        return tuple(granted)

    def install_blocking_stop_hook(self) -> Path:
        """Install a Stop hook that continues the session one time.

        Returns:
            The marker path written when the hook runs.

        """
        claude_directory = Path(self.working_directory) / ".claude"
        hook_directory = claude_directory / HOOKS_DIRECTORY_NAME
        hook_directory.mkdir(parents=True, exist_ok=True)
        script = hook_directory / "blocking_stop.py"
        script.write_text(
            r"""from __future__ import annotations

import json
import sys
from pathlib import Path


request = json.load(sys.stdin)
if request.get("stop_hook_active"):
    raise SystemExit(0)

Path(__file__).with_name("blocking-stop.started").write_text(
    "started\n",
    encoding="utf-8",
)
print(json.dumps({
    "decision": "block",
    "reason": (
        "Run the exact foreground Bash command `sleep 20`. Wait for it. "
        "Then reply only with BLOCKED_STOP_CONTINUED."
    ),
}))
""",
            encoding=TEXT_ENCODING,
        )
        (claude_directory / "settings.json").write_text(
            json.dumps(
                {
                    HOOKS_DIRECTORY_NAME: {
                        "Stop": [
                            {
                                HOOKS_DIRECTORY_NAME: [
                                    {
                                        "type": "command",
                                        "command": "python3 .claude/hooks/blocking_stop.py",
                                    },
                                ],
                            },
                        ],
                    },
                },
            ),
            encoding=TEXT_ENCODING,
        )
        return self.blocking_stop_marker

    @property
    def blocking_stop_marker(self) -> Path:
        """Process blocking stop marker."""
        return Path(self.working_directory) / ".claude" / HOOKS_DIRECTORY_NAME / "blocking-stop.started"

    def remove_linked_worktree(self) -> None:
        """Remove the linked test worktree.

        Raises:
            AssertionError: If the worktree directory remains after removal.

        """
        git_commands.run(
            Path(self.repository_root),
            "worktree",
            "remove",
            "--force",
            self.working_directory,
        )
        if Path(self.working_directory).exists():
            message = "the linked worktree directory still exists"
            raise AssertionError(message)


@dataclass
class ClaudeCodeProjectTrust:
    """Represent claude code project trust."""

    state_file: Path
    working_directory: str
    previous: dict[str, Any] | None
    existed: bool

    @classmethod
    def grant(
        cls,
        state_file: Path,
        working_directory: str,
    ) -> ClaudeCodeProjectTrust:
        """Store temporary trust for one Claude project.

        Returns:
            The trust record with the previous project state.

        Raises:
            TypeError: If the projects field is not a JSON object.

        """
        with _locked_claude_state():
            document = _read_json_object(state_file)
            projects = document.setdefault("projects", {})
            if not isinstance(projects, dict):
                msg = f"Claude Code projects are not an object in {state_file}"
                raise TypeError(
                    msg,
                )
            previous, existed = _grant_project_trust(
                projects,
                working_directory,
            )
            _write_json_object(state_file, document)
        return cls(state_file, working_directory, previous, existed)

    def close(self) -> None:
        """Restore the project state that existed before trust was granted.

        Raises:
            TypeError: If the projects field is not a JSON object.

        """
        with _locked_claude_state():
            document = _read_json_object(self.state_file)
            projects = document.get("projects")
            if not isinstance(projects, dict):
                message = f"Claude Code projects are not an object in {self.state_file}"
                raise TypeError(
                    message,
                )
            if self.existed:
                projects[self.working_directory] = self.previous
            else:
                projects.pop(self.working_directory, None)
            _write_json_object(self.state_file, document)


type ProjectRecord = dict[str, Any]


def _grant_project_trust(
    projects: dict[Any, Any],
    working_directory: str,
) -> tuple[ProjectRecord | None, bool]:
    """Set project trust and preserve the prior project record.

    Returns:
        The prior project record and whether it existed.

    """
    stored_project = projects.get(working_directory)
    existed = working_directory in projects
    previous = dict(stored_project) if isinstance(stored_project, dict) else None
    trusted = dict(previous or {})
    trusted.update(
        {
            "allowedTools": [],
            "mcpContextUris": [],
            "mcpServers": {},
            "enabledMcpjsonServers": [],
            "disabledMcpjsonServers": [],
            "hasTrustDialogAccepted": True,
            "projectOnboardingSeenCount": 0,
            "hasClaudeMdExternalIncludesApproved": False,
            "hasClaudeMdExternalIncludesWarningShown": False,
        },
    )
    projects[working_directory] = trusted
    return previous, existed


@contextmanager
def _locked_claude_state() -> Iterator[None]:
    lock_path = Path(tempfile.gettempdir()) / (f"baqylau-e2e-claude-state-{os.getuid()}.lock")
    with lock_path.open("a+", encoding=TEXT_ENCODING) as lock, ExitStack() as cleanup:
        fcntl.flock(lock, fcntl.LOCK_EX)
        cleanup.callback(fcntl.flock, lock, fcntl.LOCK_UN)
        yield


def _read_json_object(path: Path) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding=TEXT_ENCODING))
    if not isinstance(document, dict):
        message = f"Claude Code state is not an object in {path}"
        raise TypeError(message)
    return document


def _write_json_object(path: Path, document: dict[str, Any]) -> None:
    mode = stat.S_IMODE(path.stat().st_mode)
    with tempfile.TemporaryDirectory(
        dir=path.parent,
        prefix=f".{path.name}.",
    ) as temporary_directory:
        temporary_path = Path(temporary_directory) / path.name
        with temporary_path.open("w", encoding=TEXT_ENCODING) as target:
            json.dump(document, target, ensure_ascii=False, separators=(",", ":"))
            target.write("\n")
            target.flush()
            os.fsync(target.fileno())
            os.fchmod(target.fileno(), mode)
        temporary_path.replace(path)
