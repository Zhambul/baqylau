# Copyright (c) 2026 Zhambyl Yermagambet
"""Resolve configured inference provider executables."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from domain.ids import HarnessName

if TYPE_CHECKING:
    from harness.runtime import HarnessRuntimeConfig, HarnessRuntimeConfigs


def configured_executable(config: HarnessRuntimeConfig) -> str | None:
    """Resolve one configured executable path.

    Returns:
        Text result.

    """
    configured_path = Path(config.executable).expanduser().resolve()
    if Path(config.executable).parent != Path():
        if configured_path.is_file() and os.access(configured_path, os.X_OK):
            return str(configured_path)
        return None
    return shutil.which(config.executable)


def runtime_executable(
    runtime_configs: HarnessRuntimeConfigs,
    name: str,
) -> str | None:
    """Resolve an executable through its harness runtime configuration.

    Returns:
        Text result.

    """
    harness = HarnessName("claude_code") if name == "claude" else HarnessName("codex")
    return configured_executable(runtime_configs.for_harness(harness))
