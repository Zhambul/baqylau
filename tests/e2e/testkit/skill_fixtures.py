"""Local skill fixtures and cross-harness skill work."""

from __future__ import annotations

import os
import re
import shlex
from dataclasses import dataclass
from pathlib import Path

from tests.e2e.testkit.references import SessionSpec, WorkerKind
from tests.e2e.testkit.work import StartedWork, WorkDriver

SKILL_FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures" / "skills"
SKILL_NAME = re.compile(r"^[a-z0-9][a-z0-9-]*$")
HARNESS_SKILL_ROOTS = {
    "codex": Path(".agents/skills"),
    "claude_code": Path(".claude/skills"),
}


@dataclass(frozen=True)
class AvailableSkill:
    name: str
    source: Path
    installed: Path


class SkillFixtures:
    """Install test-owned skills in the configured E2E workspace."""

    def __init__(self, workspace: str) -> None:
        self._workspace = Path(workspace).resolve()
        self._available: dict[tuple[str, str], AvailableSkill] = {}
        self._links: list[tuple[Path, Path]] = []
        self._created_directories: list[Path] = []

    def make_available(self, harness: str, name: str) -> AvailableSkill:
        key = (harness, name)
        if key in self._available:
            return self._available[key]
        if not SKILL_NAME.fullmatch(name):
            raise AssertionError(f"invalid test skill name {name!r}")
        try:
            native_root = HARNESS_SKILL_ROOTS[harness]
        except KeyError as error:
            raise AssertionError(f"harness {harness!r} has no skill fixture location") from error

        fixture_root = SKILL_FIXTURE_ROOT.resolve()
        source = (fixture_root / name).resolve()
        try:
            source.relative_to(fixture_root)
        except ValueError as error:
            raise AssertionError(f"test skill {name!r} is outside the fixture root") from error
        skill_file = source / "SKILL.md"
        if not skill_file.is_file():
            raise AssertionError(f"test skill {name!r} has no SKILL.md")
        if f"\nname: {name}\n" not in f"\n{skill_file.read_text(encoding='utf-8')}":
            raise AssertionError(f"test skill {name!r} has a different metadata name")

        destination = self._workspace / native_root / name
        self._make_parent(destination.parent)
        if os.path.lexists(destination):
            if destination.is_symlink() and destination.resolve() == source:
                available = AvailableSkill(name, source, destination)
                self._available[key] = available
                return available
            raise AssertionError(f"test skill destination already exists: {destination}")
        destination.symlink_to(source, target_is_directory=True)
        self._links.append((destination, source))
        available = AvailableSkill(name, source, destination)
        self._available[key] = available
        return available

    def _make_parent(self, directory: Path) -> None:
        missing: list[Path] = []
        current = directory
        while current != self._workspace and not current.exists():
            missing.append(current)
            current = current.parent
        directory.mkdir(parents=True, exist_ok=True)
        self._created_directories.extend(reversed(missing))

    def close(self) -> None:
        for destination, source in reversed(self._links):
            if not os.path.lexists(destination):
                continue
            if not destination.is_symlink() or destination.resolve() != source:
                raise AssertionError(f"test skill link changed during the scenario: {destination}")
            destination.unlink()
        for directory in reversed(self._created_directories):
            try:
                directory.rmdir()
            except OSError:
                pass


class SkillWorkDriver:
    """Start named work that invokes one test-owned skill."""

    def __init__(self, work_driver: WorkDriver, skill_fixtures: SkillFixtures) -> None:
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
                f"Use the Skill tool exactly once with skill {skill.name} and no arguments. "
                "Then follow the loaded skill instructions."
            )
        else:
            raise AssertionError(f"harness {spec.harness!r} has no skill work adapter")
        return self._work_driver.launch(
            spec,
            work_name=work_name,
            worker_kind=worker_kind,
            prompt=prompt,
        )
