# Copyright (c) 2026 Zhambyl Yermagambet
"""Build private process inputs for preparation runner tests."""

import os
import sys
from pathlib import Path

from extensions.models.processes import PreparationCommand


def command(directory: Path, source: str, *, timeout: float = 5, limit: int = 4096) -> PreparationCommand:
    """Run a small isolated Python program with no inherited application settings.

    Returns:
        A private subprocess request with explicit bounds.

    """
    return PreparationCommand(
        (sys.executable, "-I", "-c", source), {"PATH": os.defpath}, directory,
        timeout_seconds=timeout, output_limit=limit,
    )
