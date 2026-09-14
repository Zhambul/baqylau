# Copyright (c) 2026 Zhambyl Yermagambet
"""Checks for test-owned skill fixtures and skill work."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest

from tests.e2e.testkit.references import SessionSpec, WorkerKind
from tests.e2e.testkit.skill_fixtures import (
    SKILL_FIXTURE_ROOT,
    SkillFixtures,
    SkillWorkDriver,
)

if TYPE_CHECKING:
    from api.controls.models.attachment_reference import AttachmentReferenceBody
    from tests.e2e.testkit.work_models import StartedWork, WorkRequest

SKILL_NAME = "baqylau-e2e-communication"


@pytest.mark.parametrize(
    ("harness", "native_root"),
    [
        ("codex", Path(".agents/skills")),
        ("claude_code", Path(".claude/skills")),
        ("opencode2", Path(".agents/skills")),
    ],
)
def test_skill_fixture_uses_one_test_owned_source(
    tmp_path: Path,
    harness: str,
    native_root: Path,
) -> None:
    """Verify skill fixture uses one test owned source."""
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
    """Represent capturing work driver."""

    def __init__(self) -> None:
        """Initialize the object."""
        self.prompt = ""
        self.launch_options: tuple[str, WorkerKind, tuple[AttachmentReferenceBody, ...]] | None = None

    def launch(self, _spec: SessionSpec, request: WorkRequest) -> StartedWork:
        """Record the skill work launch request.

        Returns:
            The fixed test marker with the started-work type.

        """
        self.prompt = request.prompt
        self.launch_options = (request.name, request.worker_kind, request.attachments)
        return cast("StartedWork", "started")


@pytest.mark.parametrize(
    ("harness", "expected_prompt"),
    [
        ("codex", "$baqylau-e2e-communication"),
        (
            "opencode2",
            (
                "Use the skill tool exactly once to load baqylau-e2e-communication. "
                "Then follow the loaded skill instructions."
            ),
        ),
        (
            "claude_code",
            (
                "Use the Skill tool exactly once with skill "
                "baqylau-e2e-communication and argument baqylau-e2e-argument. "
                "Then follow the loaded skill instructions."
            ),
        ),
    ],
)
def test_skill_work_adapter_owns_native(
    tmp_path: Path,
    harness: str,
    expected_prompt: str,
) -> None:
    """Verify skill work adapter owns the native invocation prompt."""
    work_driver = CapturingWorkDriver()
    fixtures = SkillFixtures(str(tmp_path))
    driver = SkillWorkDriver(work_driver, fixtures)

    driver.launch(
        SessionSpec(harness, "model", "low"),
        work_name="skill work",
        worker_kind=WorkerKind.LEAD,
        skill_name=SKILL_NAME,
    )

    assert work_driver.prompt == expected_prompt
    fixtures.close()


def test_codex_subagent_reads_installed_test(tmp_path: Path) -> None:
    """Verify codex subagent reads the installed test skill."""
    work_driver = CapturingWorkDriver()
    fixtures = SkillFixtures(str(tmp_path))
    driver = SkillWorkDriver(work_driver, fixtures)

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
