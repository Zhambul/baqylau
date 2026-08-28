"""Input operations — typing into and keying into a window."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from terminal.models.values import WindowId


class TextInputMode(StrEnum):
    TYPE = "type"
    PASTE = "paste"


@dataclass(frozen=True)
class TextInsertRequest:
    """Put `text` in a window without an Enter key.

    `mode=PASTE` delivers the text as one bracketed paste. This operation is
    for draft text that must stay in the terminal composer.
    """

    window_id: WindowId
    text: str
    mode: TextInputMode


@dataclass(frozen=True)
class TextInsertResponse:
    succeeded: bool
    reason: str | None = None


@dataclass(frozen=True)
class TextSubmitRequest:
    """Deliver `text` to a window, followed by Enter.

    `mode=PASTE` delivers it as ONE atomic bracketed paste. A typed delivery
    is read as fast individual keystrokes, and a TUI whose input just changed
    state (right after a cancel cleared its draft) drops the leading bytes; a
    paste is read whole. The Enter stays a separate keystroke either way, so it
    still submits rather than becoming a newline in the draft.
    """

    window_id: WindowId
    text: str
    mode: TextInputMode


@dataclass(frozen=True)
class TextSubmitResponse:
    succeeded: bool
    reason: str | None = None


@dataclass(frozen=True)
class KeySendRequest:
    """A key EVENT ("escape", "ctrl+c"), encoded for the program's current
    keyboard mode — raw bytes bypass it, and a TUI speaking an enhanced
    keyboard protocol never sees a bare \\x1b as Escape."""

    window_id: WindowId
    key: str


@dataclass(frozen=True)
class KeySendResponse:
    succeeded: bool
    reason: str | None = None
