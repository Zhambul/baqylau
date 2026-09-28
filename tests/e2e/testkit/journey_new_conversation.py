# Copyright (c) 2026 Zhambyl Yermagambet
"""Answer the question that Codex asks before a native `/new` takes a prompt."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from terminal import models as terminal_models

if TYPE_CHECKING:
    from terminal.contract import TerminalPlugin

# Codex 0.156 asks where the new conversation runs; the first choice keeps the checkout.
PICKER = "Where should the new conversation run?"
PICKER_SECONDS = 5.0
POLL_SECONDS = 0.2


def choose_place(terminal: TerminalPlugin, window: str) -> None:
    """Choose the current checkout when the picker appears; another harness shows none."""
    window_id = terminal_models.values.WindowId(window)
    request = terminal_models.viewport.ScreenReadRequest(window_id)
    deadline = time.monotonic() + PICKER_SECONDS
    while time.monotonic() < deadline:
        if PICKER in (terminal.viewport.read_screen(request).text or ""):
            outcome = terminal.input.send_key(terminal_models.input.KeySendRequest(window_id, "enter"))
            assert outcome.succeeded, f"the new conversation choice was not delivered: {outcome.reason}"
            return
        time.sleep(POLL_SECONDS)
