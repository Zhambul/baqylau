# Copyright (c) 2026 Zhambyl Yermagambet
"""Submit one native slash command through the session composer.

The native commands are the same ones a person types. They are used in place
of the key bindings that reach the same commands, because a binding depends on
the leader key and on whatever the person has configured, while the command
name does not. The native list also holds `/models`, `/undo` and `/redo`; each
is named here when a control drives it.

The command goes in as a BRACKETED PASTE. The composer takes a paste as
content, never as keystrokes, so a command cannot be read as a key sequence.
"""

from harness.models import controls
from terminal.models.input import TextInputMode, TextSubmitRequest
from terminal.models.values import WindowId

COMPACT = "/compact"
VARIANTS = "/variants"


def submit(control_context: controls.ControlContext, command: str) -> str | None:
    """Send one native slash command to the owned terminal.

    Returns:
        An error, or None when the terminal took the command.

    """
    window_id = control_context.terminal_window_id
    if window_id is None:
        return "session has no terminal"
    sent = control_context.terminal.input.submit_text(
        TextSubmitRequest(WindowId(str(window_id)), command, TextInputMode.PASTE),
    )
    if sent.succeeded:
        return None
    return sent.reason or f"native {command} was not submitted"
