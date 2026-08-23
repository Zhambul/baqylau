"""Checks for test-owned skill fixtures and skill work."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.e2e.testkit.references import SessionSpec, WorkerKind
from tests.e2e.testkit.skill_fixtures import (
    SKILL_FIXTURE_ROOT,
    SkillFixtures,
    SkillWorkDriver,
)

SKILL_NAME = "baqylau-e2e-communication"


@pytest.mark.parametrize(
    ("harness", "native_root"),
    (
        ("codex", Path(".agents/skills")),
        ("claude_code", Path(".claude/skills")),
    ),
)
def test_skill_fixture_uses_one_test_owned_source(
    tmp_path: Path,
    harness: str,
    native_root: Path,
) -> None:
    fixtures = SkillFixtures(str(tmp_path))

    available = fixtures.make_available(harness, SKILL_NAME)
    link = tmp_path / native_root / SKILL_NAME

    assert available.source == (SKILL_FIXTURE_ROOT / SKILL_NAME).resolve()
    assert available.installed == link
    assert link.is_symlink()
    assert link.resolve() == available.source

    fixtures.close()
    assert not os.path.lexists(link)


class CapturingWorkDriver:
    def __init__(self) -> None:
        self.prompt = ""

    def launch(self, _spec, **arguments):
        self.prompt = arguments["prompt"]
        return "started"


@pytest.mark.parametrize(
    ("harness", "expected_prompt"),
    (
        ("codex", "$baqylau-e2e-communication"),
        (
            "claude_code",
            "Use the Skill tool exactly once with skill "
            "baqylau-e2e-communication and no arguments. "
            "Then follow the loaded skill instructions.",
        ),
    ),
)
def test_skill_work_adapter_owns_the_native_invocation_prompt(
    tmp_path: Path,
    harness: str,
    expected_prompt: str,
) -> None:
    work_driver = CapturingWorkDriver()
    fixtures = SkillFixtures(str(tmp_path))
    driver = SkillWorkDriver(work_driver, fixtures)  # type: ignore[arg-type]

    driver.launch(
        SessionSpec(harness, "model", "low"),
        work_name="skill work",
        worker_kind=WorkerKind.LEAD,
        skill_name=SKILL_NAME,
    )

    assert work_driver.prompt == expected_prompt
    fixtures.close()


def test_codex_subagent_reads_the_installed_test_skill(tmp_path: Path) -> None:
    work_driver = CapturingWorkDriver()
    fixtures = SkillFixtures(str(tmp_path))
    driver = SkillWorkDriver(work_driver, fixtures)  # type: ignore[arg-type]

    driver.launch(
        SessionSpec("codex", "model", "low"),
        work_name="skill work",
        worker_kind=WorkerKind.SUBAGENT,
        skill_name=SKILL_NAME,
    )

    skill_file = tmp_path / ".agents/skills" / SKILL_NAME / "SKILL.md"
    assert work_driver.prompt == (
        f"Use test skill {SKILL_NAME}. To load it, run exactly this shell command: "
        f"cat {skill_file}. Then follow the loaded instructions."
    )
    fixtures.close()
