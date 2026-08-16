"""Terminal value objects — colours, tab appearance, and the window entity.

Value objects and entities, not messages: they carry no Request/Response suffix
because they are what the operations are *about*, not the operations themselves.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


# Generic window-metadata keys — baqylau's own names for "this window serves
# session X" / "this is the mirror pane" / "this is the scoreboard pane". Each
# terminal renders them in whatever per-window metadata it has; nothing above
# this layer knows that mechanism, or that one terminal calls it variables.
#
# The session key is a LIVENESS cross-check, not the session→window mapping:
# that mapping is evidence (`Session.terminal_window_id`, kept current from the
# envelope of every hook-borne fact). The two pane keys are load-bearing: the
# mirror and scoreboard panes are daemon-created, so nothing else records them
# and the terminal must be able to find them again after a daemon restart.
SESSION_WINDOW_TAG = "baqylau_session"
ACTIVITY_PANE_TAG = "baqylau_activity"
SCOREBOARD_PANE_TAG = "baqylau_scoreboard"


@dataclass(frozen=True)
class RGB:
    red: int
    green: int
    blue: int

    def __post_init__(self) -> None:
        if not all(0 <= component <= 255 for component in (self.red, self.green, self.blue)):
            raise ValueError("RGB components must be between 0 and 255")


@dataclass(frozen=True)
class TabAppearance:
    active_background: RGB
    active_foreground: RGB
    inactive_background: RGB
    inactive_foreground: RGB


@dataclass(frozen=True)
class WindowInfo:
    """One window, as the terminal reports it.

    `is_first_in_tab` is creation order, which identifies the HOST pane: the
    session's own window is its tab's first window, and the mirror/scoreboard
    are split in after it. `tab_is_active` is "selected inside its OS window";
    `tab_is_focused` additionally requires that OS window to hold keyboard
    focus — a tab merely selected inside a BACKGROUNDED terminal (a session the
    web dashboard just spawned while you are on your phone) is active but not
    focused.
    """

    window_id: str
    tab_id: str
    tags: Mapping[str, str]
    columns: int
    lines: int
    is_first_in_tab: bool
    tab_is_active: bool
    tab_is_focused: bool
