# Copyright (c) 2026 Zhambyl Yermagambet
"""Select one entry in a native command dialog."""

from harness.impl.opencode2 import native_commands, native_screen
from harness.models.controls import ControlContext
from terminal.models.input import KeySendRequest, TextInputMode, TextInsertRequest
from terminal.models.values import WindowId

DIALOG_SECONDS = 5


def choose(control_context: ControlContext, command: str, title: str, search_text: str) -> str | None:
    """Open a native list, enter its search text, and select the result.

    Returns:
        A failure reason, or None after submission.

    """
    reason = native_commands.submit(control_context, command)
    if reason is not None:
        return reason
    return search(control_context, title, search_text)


def search(control_context: ControlContext, title: str, search_text: str) -> str | None:
    """Select an entry in a dialog that the native application opened.

    Returns:
        A failure reason, or None after submission.

    """
    if not native_screen.wait_for(control_context, lambda screen: title in screen, DIALOG_SECONDS):
        return f"native {title} dialog did not open"
    window_id = WindowId(str(control_context.terminal_window_id))
    typed = control_context.terminal.input.insert_text(TextInsertRequest(window_id, search_text, TextInputMode.TYPE))
    if not typed.succeeded:
        return typed.reason or "native selection search could not be filled"
    sent = control_context.terminal.input.send_key(KeySendRequest(window_id, "enter"))
    return None if sent.succeeded else sent.reason or "native selection was not submitted"
