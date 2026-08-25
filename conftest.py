"""Shared safety settings and E2E step plug-ins for the complete test suite."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest_plugins = (
    "tests.e2e.steps.accounts",
    "tests.e2e.steps.browser",
    "tests.e2e.steps.catalog",
    "tests.e2e.steps.attachments",
    "tests.e2e.steps.compactions",
    "tests.e2e.steps.controls",
    "tests.e2e.steps.files",
    "tests.e2e.steps.feed",
    "tests.e2e.steps.insights",
    "tests.e2e.steps.journeys",
    "tests.e2e.steps.planning",
    "tests.e2e.steps.preferences",
    "tests.e2e.steps.questions",
    "tests.e2e.steps.reasoning",
    "tests.e2e.steps.restarts",
    "tests.e2e.steps.scoreboard",
    "tests.e2e.steps.sessions",
    "tests.e2e.steps.shells",
    "tests.e2e.steps.skills",
    "tests.e2e.steps.statuses",
    "tests.e2e.steps.subagents",
    "tests.e2e.steps.terminals",
    "tests.e2e.steps.usage",
    "tests.e2e.steps.web",
    "tests.e2e.steps.worktrees",
    "tests.e2e.steps.work",
)


@pytest.fixture(autouse=True)
def isolated_application_files(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Keep application state out of the user's data directory."""
    monkeypatch.setenv("BAQYLAU_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))
    monkeypatch.setenv("BAQYLAU_TERMINAL", "none")
    monkeypatch.delenv("KITTY_WINDOW_ID", raising=False)
