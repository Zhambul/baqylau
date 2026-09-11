# Copyright (c) 2026 Zhambyl Yermagambet
"""Declare Claude Code and its default runtime."""

from pathlib import Path

from domain.ids import HarnessName
from harness.executable import installed_executable
from harness.models.definition import HarnessDefinition
from harness.models.runtime import HarnessRuntimeConfig


def default_runtime_config() -> HarnessRuntimeConfig:
    """Resolve Claude Code without building its plugin services.

    Returns:
        The installed runtime configuration.

    """
    home = Path.home()
    return HarnessRuntimeConfig(
        installed_executable(
            (str(home / ".local/bin/claude"), "/opt/homebrew/bin/claude", "/usr/local/bin/claude"),
            "claude",
        ),
        home / ".claude",
        use_vendor_default_configuration=True,
    )


DEFINITION = HarnessDefinition(HarnessName("claude_code"), default_runtime_config)
