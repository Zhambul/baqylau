# Copyright (c) 2026 Zhambyl Yermagambet
"""Describe the runtime selected for one harness."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class HarnessRuntimeConfig:
    """Hold the executable and configuration paths."""

    executable: str
    configuration_directory: Path
    settings_file: Path | None = None
    use_vendor_default_configuration: bool = False
