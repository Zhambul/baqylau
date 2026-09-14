# Copyright (c) 2026 Zhambyl Yermagambet
"""Submit the initial native draft after the input is ready."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from terminal.models.input import KeySendRequest
from terminal.models.viewport import ScreenReadRequest

if TYPE_CHECKING:
    from collections.abc import Iterator

    from terminal.contract import TerminalPlugin
    from terminal.models.values import WindowId

STARTUP_SECONDS = 30
SCREEN_INTERVAL_SECONDS = 0.1
PROMPT_TAIL_LENGTH = 40
SUBMISSION_INTERVAL_SECONDS = 0.5
START_CONTROLS = "shift+tab agents"
MODEL_FOOTER = "Build ·"
# Only the start page draws the native logo. The idle controls do not mark
# that page: a session keeps them in its own footer until its turn begins.
# A check on the controls alone therefore waited for a RUNNING TURN, and a
# model that was slow to start looked the same as a draft that never went.
START_LOGO = "█▀▀█"


def submit(terminal_plugin: TerminalPlugin, window_id: WindowId, prompt: str) -> str | None:
    """Submit the draft when the native input controls are visible.

    Returns:
        An error, or None when the native start page closes.

    """
    ready_before = False
    submitted = False
    for screen in _screens(terminal_plugin, window_id):
        if submitted and screen and START_LOGO not in screen:
            return None
        # The draft goes when it is READY TWICE. A whole screen that does not
        # change is not used as the signal, because a part of the screen that
        # this does not read can change while another session of the project
        # runs.
        ready = _ready(screen, prompt)
        if ready and ready_before:
            reason = _press(terminal_plugin, window_id)
            if reason:
                return reason
            submitted = True
        ready_before = ready
    return _expired(terminal_plugin, window_id, submitted=submitted)


def _screens(terminal_plugin: TerminalPlugin, window_id: WindowId) -> Iterator[str | None]:
    deadline = time.monotonic() + STARTUP_SECONDS
    while time.monotonic() < deadline:
        yield terminal_plugin.viewport.read_screen(ScreenReadRequest(window_id)).text
        time.sleep(SCREEN_INTERVAL_SECONDS)


def _expired(terminal_plugin: TerminalPlugin, window_id: WindowId, *, submitted: bool) -> str:
    """Name the part of the start page that did not arrive.

    Returns:
        The reason that a person, and a test report, can act on.

    """
    if submitted:
        return "OpenCode2 kept its start page after the draft was submitted"
    screen = terminal_plugin.viewport.read_screen(ScreenReadRequest(window_id)).text
    if not screen:
        return "OpenCode2 gave no screen"
    if START_LOGO not in screen:
        return "OpenCode2 did not draw its start page"
    if "ctrl+p commands" not in screen or START_CONTROLS not in screen:
        return "OpenCode2 did not draw its start page controls"
    return "OpenCode2 did not show the draft of the first prompt"


def _press(terminal_plugin: TerminalPlugin, window_id: WindowId) -> str | None:
    sent = terminal_plugin.input.send_key(KeySendRequest(window_id, "enter"))
    time.sleep(SUBMISSION_INTERVAL_SECONDS)
    return None if sent.succeeded else sent.reason or "OpenCode2 draft was not submitted"


def _ready(screen: str | None, prompt: str) -> bool:
    if screen is None or START_LOGO not in screen:
        return False
    if "ctrl+p commands" not in screen or START_CONTROLS not in screen:
        return False
    # The composer can appear before the native model catalog is ready.
    # Enter at that point opens the integration picker instead of a session.
    if MODEL_FOOTER not in screen:
        return False
    # Screen wrapping can split a long word without a space in the draft.
    visible = "".join(screen.replace("┃", " ").split())
    expected = " ".join(prompt.split())[-PROMPT_TAIL_LENGTH:]
    return "".join(expected.split()) in visible
