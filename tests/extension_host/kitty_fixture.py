# Copyright (c) 2026 Zhambyl Yermagambet
"""Drive the running Kitty through its remote-control program, and close only the test's own windows."""

from __future__ import annotations

import os
import shutil
import subprocess  # noqa: S404 -- Drive the real Kitty through its own remote-control program.
import time
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

KITTEN_SECONDS = 20
WAIT_SECONDS = 30
POLL_SECONDS = 0.2
KITTEN = shutil.which("kitten") or ""


def kitten(*arguments: str) -> str:
    """Run one Kitty remote-control command.

    Returns:
        Its output.

    """
    command = (KITTEN, "@", *arguments)
    finished = subprocess.run(  # noqa: S603 -- Fixed kitten arguments, no shell.
        command, capture_output=True, text=True, check=True, timeout=KITTEN_SECONDS,
    )
    return finished.stdout


def screen(window_id: str) -> str:
    """Read the visible text of one window.

    Returns:
        The text.

    """
    return kitten("get-text", "--match", f"id:{window_id}")


def wait_for_text(window_id: str, wanted: Callable[[str], bool], what: str) -> None:
    """Read the window until its text meets the condition."""
    deadline = time.monotonic() + WAIT_SECONDS
    text = screen(window_id)
    while not wanted(text):
        assert time.monotonic() < deadline, f"{what}; last screen: {text!r}"
        time.sleep(POLL_SECONDS)
        text = screen(window_id)


def require_kitty() -> None:
    """Skip unless real Kitty cases are enabled and a remote-control socket exists."""
    ready = (os.environ.get("CLAUDE_E2E_KITTY"), os.environ.get("KITTY_LISTEN_ON"), KITTEN)
    if not all(ready):
        pytest.skip("real Kitty cases are opt-in (CLAUDE_E2E_KITTY=1) and need kitten and a remote-control socket")


@pytest.fixture
def anchor_window() -> Iterator[str]:
    """Open an anchor window in the running Kitty, and close its tab after the test.

    Yields:
        The anchor window ID.

    """
    require_kitty()
    anchor = kitten("launch", "--type=tab", "--keep-focus", "--tab-title", "c20-anchor", "sleep", "600").strip()
    try:
        yield anchor
    finally:
        # The pane opens in the anchor's own tab, so closing the tab closes only the test's windows.
        subprocess.run(  # noqa: S603 -- Fixed kitten arguments, no shell.
            (KITTEN, "@", "close-tab", "--match", f"window_id:{anchor}"),
            capture_output=True, check=False, timeout=KITTEN_SECONDS,
        )
