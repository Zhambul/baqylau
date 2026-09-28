# Copyright (c) 2026 Zhambyl Yermagambet
"""Wait for a host condition with a deadline and a clear failure message."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

POLL_SECONDS = 0.1


class HostWaitError(TimeoutError):
    """Report a host condition that did not become true in time."""


def wait_until[Found](
    read: Callable[[], Found | None],
    seconds: float,
    description: Callable[[], str],
) -> Found:
    """Read until the reader gives a value.

    Returns:
        The first value that is not None.

    Raises:
        HostWaitError: If the deadline comes first; the message tells the last state.

    """
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        found = read()
        if found is not None:
            return found
        time.sleep(POLL_SECONDS)
    state = description()
    message = f"after {seconds} seconds: {state}"
    raise HostWaitError(message)
