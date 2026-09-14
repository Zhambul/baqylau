# Copyright (c) 2026 Zhambyl Yermagambet
"""Declare the installed OpenCode2 runtime."""

from pathlib import Path

from harness.executable import installed_executable
from harness.impl.opencode2.sources import HARNESS
from harness.models.definition import HarnessDefinition
from harness.models.runtime import HarnessRuntimeConfig


def default_runtime_config() -> HarnessRuntimeConfig:
    """Locate the installed native CLI.

    Returns:
        The executable and plugin log directory.

    """
    return HarnessRuntimeConfig(
        installed_executable((str(Path.home() / ".hermes/node/bin/opencode2"),), "opencode2"),
        Path.home() / ".local/share/baqylau/opencode2",
    )


DEFINITION = HarnessDefinition(HARNESS, default_runtime_config)
