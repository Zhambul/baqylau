# Copyright (c) 2026 Zhambyl Yermagambet
"""Find an executable among a plugin's candidate paths."""

import os
from pathlib import Path


def installed_executable(candidates: tuple[str, ...], fallback: str) -> str:
    """Select the first executable file.

    Returns:
        An installed path, or the command to resolve through PATH.

    """
    for candidate in candidates:
        if Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return candidate
    return fallback
