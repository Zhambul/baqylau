# Copyright (c) 2026 Zhambyl Yermagambet
"""Check the environment paths for isolated terminal journeys."""

from __future__ import annotations

from typing import TYPE_CHECKING

from terminal.models.tabs import EnvironmentVariable
from tests.e2e.testkit.journey_contexts import IsolatedHarnessHomes

if TYPE_CHECKING:
    from pathlib import Path


def test_launch_environment_uses_isolated_homes(tmp_path: Path) -> None:
    """Keep all harness configuration paths inside the supplied test homes."""
    homes = IsolatedHarnessHomes(tmp_path / "codex", tmp_path / "claude", tmp_path / "state")
    assert homes.launch_environment() == (
        EnvironmentVariable("CODEX_HOME", str(tmp_path / "codex")),
        EnvironmentVariable("CLAUDE_CONFIG_DIR", str(tmp_path / "claude")),
        EnvironmentVariable("CLAUDE_CODE_MANAGED_SETTINGS_PATH", str(tmp_path / "claude" / "managed-settings.json")),
        EnvironmentVariable("XDG_STATE_HOME", str(tmp_path / "state")),
    )
