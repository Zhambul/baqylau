"""Claude Code's macOS clipboard-image guard."""

from __future__ import annotations

import subprocess
import sys

IMAGE_FLAVORS = ("PNGf", "TIFF", "8BPS", "jp2", "GIF", "JPEG", "picture")


def has_image() -> bool:
    if sys.platform != "darwin":
        return False
    try:
        result = subprocess.run(
            ["osascript", "-e", "clipboard info"],
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return any(flavor in (result.stdout or "") for flavor in IMAGE_FLAVORS)


def clear_image() -> bool:
    if not has_image():
        return False
    try:
        subprocess.run(
            ["osascript", "-e", 'set the clipboard to ""'],
            capture_output=True,
            timeout=2,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return True
