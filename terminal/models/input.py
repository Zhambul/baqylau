"""Input operations — typing into and keying into a window."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class TextSubmitRequest:
    """Deliver `text` to a window, followed by Enter.

    `mode="paste"` delivers it as ONE atomic bracketed paste. A typed delivery
    is read as fast individual keystrokes, and a TUI whose input just changed
    state (right after a cancel cleared its draft) drops the leading bytes; a
    paste is read whole. The Enter stays a separate keystroke either way, so it
    still submits rather than becoming a newline in the draft.
    """

    window_id: str
    text: str
    mode: Literal["type", "paste"]


@dataclass(frozen=True)
class TextSubmitResponse:
    succeeded: bool
    reason: str | None = None


@dataclass(frozen=True)
class KeySendRequest:
    """A key EVENT ("escape", "ctrl+c"), encoded for the program's current
    keyboard mode — raw bytes bypass it, and a TUI speaking an enhanced
    keyboard protocol never sees a bare \\x1b as Escape."""

    window_id: str
    key: str


@dataclass(frozen=True)
class KeySendResponse:
    succeeded: bool
    reason: str | None = None
