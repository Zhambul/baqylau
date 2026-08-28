"""An isolated real Git worktree for repository-state cases."""

from __future__ import annotations

import json
import fcntl
import os
import stat
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any



@dataclass(frozen=True)
class RepositoryWorkspace:
    working_directory: str
    repository_root: str
    branch: str
    worktree: str

    @classmethod
    def create(cls, root: Path) -> RepositoryWorkspace:
        source = root / "source-repository"
        linked = root / "e2e-linked-worktree"
        source.mkdir()
        _git(source, "init", "--initial-branch=e2e-main")
        _git(source, "config", "user.name", "Baqylau E2E")
        _git(source, "config", "user.email", "baqylau-e2e@example.invalid")
        (source / "repository-state.txt").write_text("CLEAN_REPOSITORY_STATE\n")
        _git(source, "add", "repository-state.txt")
        _git(source, "commit", "-m", "Create repository state fixture")
        _git(source, "worktree", "add", "-b", "e2e-worktree", str(linked))
        return cls(str(linked), str(source), "e2e-worktree", linked.name)

    def trust_for_codex(self, codex_home: Path) -> None:
        with (codex_home / "config.toml").open("a", encoding="utf-8") as config:
            config.write(
                f"\n[projects.{json.dumps(self.repository_root)}]\n"
                'trust_level = "trusted"\n'
            )

    def trust_for_claude_code(
        self,
        state_file: Path,
    ) -> tuple[ClaudeCodeProjectTrust, ...]:
        granted: list[ClaudeCodeProjectTrust] = []
        try:
            for directory in (self.working_directory, self.repository_root):
                granted.append(ClaudeCodeProjectTrust.grant(state_file, directory))
        except BaseException:
            for trust in reversed(granted):
                trust.close()
            raise
        return tuple(granted)

    def install_blocking_stop_hook(self) -> Path:
        """Install a Stop hook that continues the session one time."""
        claude_directory = Path(self.working_directory) / ".claude"
        hook_directory = claude_directory / "hooks"
        hook_directory.mkdir(parents=True, exist_ok=True)
        script = hook_directory / "blocking_stop.py"
        script.write_text(
            """from __future__ import annotations

import json
import sys
from pathlib import Path


request = json.load(sys.stdin)
if request.get("stop_hook_active"):
    raise SystemExit(0)

Path(__file__).with_name("blocking-stop.started").write_text(
    "started\\n",
    encoding="utf-8",
)
print(json.dumps({
    "decision": "block",
    "reason": (
        "Run the exact foreground Bash command `sleep 8`. Wait for it. "
        "Then reply only with BLOCKED_STOP_CONTINUED."
    ),
}))
""",
            encoding="utf-8",
        )
        (claude_directory / "settings.json").write_text(
            json.dumps({
                "hooks": {
                    "Stop": [{
                        "hooks": [{
                            "type": "command",
                            "command": "python3 .claude/hooks/blocking_stop.py",
                        }],
                    }],
                },
            }),
            encoding="utf-8",
        )
        return self.blocking_stop_marker

    @property
    def blocking_stop_marker(self) -> Path:
        return (
            Path(self.working_directory)
            / ".claude"
            / "hooks"
            / "blocking-stop.started"
        )

    def remove_linked_worktree(self) -> None:
        _git(
            Path(self.repository_root),
            "worktree",
            "remove",
            "--force",
            self.working_directory,
        )
        if Path(self.working_directory).exists():
            raise AssertionError("the linked worktree directory still exists")


@dataclass
class ClaudeCodeProjectTrust:
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
        with _locked_claude_state():
            document = _read_json_object(state_file)
            projects = document.setdefault("projects", {})
            if not isinstance(projects, dict):
                raise AssertionError(
                    f"Claude Code projects are not an object in {state_file}"
                )
            existed = working_directory in projects
            previous_value = projects.get(working_directory)
            previous = (
                dict(previous_value) if isinstance(previous_value, dict) else None
            )
            trusted = dict(previous or {})
            trusted.update({
                "allowedTools": [],
                "mcpContextUris": [],
                "mcpServers": {},
                "enabledMcpjsonServers": [],
                "disabledMcpjsonServers": [],
                "hasTrustDialogAccepted": True,
                "projectOnboardingSeenCount": 0,
                "hasClaudeMdExternalIncludesApproved": False,
                "hasClaudeMdExternalIncludesWarningShown": False,
            })
            projects[working_directory] = trusted
            _write_json_object(state_file, document)
        return cls(state_file, working_directory, previous, existed)

    def close(self) -> None:
        with _locked_claude_state():
            document = _read_json_object(self.state_file)
            projects = document.get("projects")
            if not isinstance(projects, dict):
                raise AssertionError(
                    f"Claude Code projects are not an object in {self.state_file}"
                )
            if self.existed:
                projects[self.working_directory] = self.previous
            else:
                projects.pop(self.working_directory, None)
            _write_json_object(self.state_file, document)


@contextmanager
def _locked_claude_state() -> Iterator[None]:
    lock_path = Path(tempfile.gettempdir()) / (
        f"baqylau-e2e-claude-state-{os.getuid()}.lock"
    )
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _read_json_object(path: Path) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise AssertionError(f"Claude Code state is not an object in {path}")
    return document


def _write_json_object(path: Path, document: dict[str, Any]) -> None:
    mode = stat.S_IMODE(path.stat().st_mode)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as target:
            temporary_path = Path(target.name)
            json.dump(document, target, ensure_ascii=False, separators=(",", ":"))
            target.write("\n")
            target.flush()
            os.fsync(target.fileno())
            os.fchmod(target.fileno(), mode)
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()

def _git(working_directory: Path, *arguments: str) -> None:
    subprocess.run(
        ("git", "-C", str(working_directory), *arguments),
        check=True,
        capture_output=True,
        text=True,
    )
