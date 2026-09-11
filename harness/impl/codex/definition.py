# Copyright (c) 2026 Zhambyl Yermagambet
"""Declare Codex and its default runtime."""

from pathlib import Path

from domain.ids import HarnessName
from harness.executable import installed_executable
from harness.models.definition import HarnessDefinition
from harness.models.runtime import HarnessRuntimeConfig

EXECUTABLE = "codex"


def default_runtime_config() -> HarnessRuntimeConfig:
    """Resolve the native Codex executable before its wrapper.

    Returns:
        The installed runtime configuration.

    """
    home = Path.home()
    packages = home / ".hermes/node/lib/node_modules/@openai/codex/node_modules/@openai"
    native = tuple(
        str(candidate) for candidate in sorted(packages.glob("codex-*/vendor/*/bin/codex"))
    )
    return HarnessRuntimeConfig(
        installed_executable(
            (
                *native,
                str(home / ".hermes/node/bin/codex"),
                "/opt/homebrew/bin/codex",
                "/usr/local/bin/codex",
                str(home / ".local/bin/codex"),
            ),
            EXECUTABLE,
        ),
        home / ".codex",
    )


DEFINITION = HarnessDefinition(HarnessName("codex"), default_runtime_config)
