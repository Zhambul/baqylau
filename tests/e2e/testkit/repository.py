"""An isolated real Git worktree for repository-state cases."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import tempfile
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

    def trust_for_claude_code(self) -> tuple[ClaudeCodeProjectTrust, ...]:
        state_file = _default_claude_code_state_file()
        granted: list[ClaudeCodeProjectTrust] = []
        try:
            for directory in (self.working_directory, self.repository_root):
                granted.append(ClaudeCodeProjectTrust.grant(state_file, directory))
        except BaseException:
            for trust in reversed(granted):
                trust.close()
            raise
        return tuple(granted)

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
        document = _read_json_object(state_file)
        projects = document.setdefault("projects", {})
        if not isinstance(projects, dict):
            raise AssertionError(f"Claude Code projects are not an object in {state_file}")
        existed = working_directory in projects
        previous_value = projects.get(working_directory)
        previous = dict(previous_value) if isinstance(previous_value, dict) else None
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


def _default_claude_code_state_file() -> Path:
    return Path.home() / ".claude.json"


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
