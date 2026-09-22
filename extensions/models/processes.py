# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep preparation subprocess requests and bounded output explicit."""

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PreparationCommand:
    """Select one command, environment, directory, and finite resource budget."""

    arguments: tuple[str, ...]
    environment: Mapping[str, str]
    directory: Path
    timeout_seconds: float = 120
    output_limit: int = 1_048_576


@dataclass(frozen=True)
class PreparationOutput:
    """Return only bounded bytes after the owned command has exited."""

    stdout: bytes
    stderr: bytes
    return_code: int
