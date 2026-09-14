# Copyright (c) 2026 Zhambyl Yermagambet
"""Local skill fixtures and cross-harness skill work."""

from __future__ import annotations

import contextlib
import os
import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Protocol

from tests.e2e.testkit.references import SessionSpec, WorkerKind
from tests.e2e.testkit.work_models import StartedWork, WorkRequest

SKILL_FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures" / "skills"
SKILL_NAME = re.compile(r"^[a-z0-9][a-z0-9-]*$")
HARNESS_SKILL_ROOTS = MappingProxyType({
    "codex": Path(".agents/skills"),
    "claude_code": Path(".claude/skills"),
    "opencode2": Path(".agents/skills"),
})


def _native_skill_root(harness: str) -> Path:
    """Return the native skill root for one harness.

    Returns:
        The native skill root for one harness.

    Raises:
        AssertionError: If the harness has no fixture location.

    """
    try:
        return HARNESS_SKILL_ROOTS[harness]
    except KeyError as error:
        message = f"harness {harness!r} has no skill fixture location"
        raise AssertionError(message) from error


def _validated_skill_source(name: str) -> Path:
    """Return a valid test skill fixture source.

    Returns:
        A valid test skill fixture source.

    Raises:
        AssertionError: If the skill fixture is not valid.

    """
    fixture_root = SKILL_FIXTURE_ROOT.resolve()
    source = (fixture_root / name).resolve()
    try:
        source.relative_to(fixture_root)
    except ValueError as error:
        message = f"test skill {name!r} is outside the fixture root"
        raise AssertionError(message) from error
    skill_file = source / "SKILL.md"
    if not skill_file.is_file():
        message = f"test skill {name!r} has no SKILL.md"
        raise AssertionError(message)
    if f"\nname: {name}\n" not in f"\n{skill_file.read_text(encoding='utf-8')}":
        message = f"test skill {name!r} has a different metadata name"
        raise AssertionError(message)
    return source


@dataclass(frozen=True)
class AvailableSkill:
    """Represent available skill."""

    name: str
    source: Path
    installed: Path


class WorkLauncher(Protocol):
    """Start one E2E work request."""

    def launch(self, spec: SessionSpec, request: WorkRequest) -> StartedWork:
        """Start work."""
        ...


class SkillFixtures:
    """Install test-owned skills in the configured E2E workspace."""

    def __init__(self, workspace: str) -> None:
        """Initialize the object."""
        self._workspace = Path(workspace).resolve()
        self._available: dict[tuple[str, str], AvailableSkill] = {}
        self._links: list[tuple[Path, Path]] = []
        self._created_directories: list[Path] = []

    def make_available(self, harness: str, name: str) -> AvailableSkill:
        """Install a test skill link, or reuse its existing matching link.

        Returns:
            The skill name, source directory, and installed path.

        Raises:
            AssertionError: If the name is invalid or the destination contains another item.

        """
        key = (harness, name)
        available = self._available.get(key)
        if available is not None:
            return available
        if not SKILL_NAME.fullmatch(name):
            msg = f"invalid test skill name {name!r}"
            raise AssertionError(msg)
        source = _validated_skill_source(name)

        destination = self._workspace / _native_skill_root(harness) / name
        self._make_parent(destination.parent)
        if os.path.lexists(destination):
            if not (destination.is_symlink() and destination.resolve() == source):
                msg = f"test skill destination already exists: {destination}"
                raise AssertionError(msg)
        else:
            destination.symlink_to(source, target_is_directory=True)
            self._links.append((destination, source))
        available = AvailableSkill(name, source, destination)
        self._available[key] = available
        return available

    def close(self) -> None:
        """Remove test-created skill links and empty parent directories.

        Raises:
            AssertionError: If a recorded link changed during the test.

        """
        for destination, source in reversed(self._links):
            if not os.path.lexists(destination):
                continue
            if not destination.is_symlink() or destination.resolve() != source:
                message = f"test skill link changed during the scenario: {destination}"
                raise AssertionError(message)
            destination.unlink()
        for directory in reversed(self._created_directories):
            with contextlib.suppress(OSError):
                directory.rmdir()

    def _make_parent(self, directory: Path) -> None:
        missing: list[Path] = []
        current = directory
        while current != self._workspace and not current.exists():
            missing.append(current)
            current = current.parent
        directory.mkdir(parents=True, exist_ok=True)
        self._created_directories.extend(reversed(missing))


class SkillWorkDriver:
    """Start named work that invokes one test-owned skill."""

    def __init__(self, work_driver: WorkLauncher, skill_fixtures: SkillFixtures) -> None:
        """Initialize the object."""
        self._work_driver = work_driver
        self._skill_fixtures = skill_fixtures

    def launch(
        self,
        spec: SessionSpec,
        *,
        work_name: str,
        worker_kind: WorkerKind,
        skill_name: str,
    ) -> StartedWork:
        """Launch work that loads one test skill.

        Returns:
            The started session and resolved work reference.

        Raises:
            AssertionError: If the harness has no skill work adapter.

        """
        skill = self._skill_fixtures.make_available(spec.harness, skill_name)
        if spec.harness == "codex":
            if worker_kind == WorkerKind.LEAD:
                prompt = f"${skill.name}"
            else:
                skill_file = shlex.quote(str(skill.installed / "SKILL.md"))
                prompt = (
                    f"Use test skill {skill.name}. To load it, run exactly this shell command: "
                    f"cat {skill_file}. Then follow the loaded instructions."
                )
        elif spec.harness == "claude_code":
            prompt = (
                f"Use the Skill tool exactly once with skill {skill.name} and argument "
                "baqylau-e2e-argument. "
                "Then follow the loaded skill instructions."
            )
        elif spec.harness == "opencode2":
            prompt = (
                f"Use the skill tool exactly once to load {skill.name}. "
                "Then follow the loaded skill instructions."
            )
        else:
            message = f"harness {spec.harness!r} has no skill work adapter"
            raise AssertionError(message)
        return self._work_driver.launch(spec, WorkRequest(work_name, prompt, worker_kind=worker_kind))
