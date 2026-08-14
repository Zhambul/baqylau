"""Harness-neutral terminal mechanics."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Mapping, Protocol

from domain.ids import SessionId


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
class TerminalResult:
    succeeded: bool
    reason: str | None = None


@dataclass(frozen=True)
class PaneRequest:
    session_id: SessionId
    command: tuple[str, ...]
    working_directory: str
    title: str


@dataclass(frozen=True)
class PaneResult:
    succeeded: bool
    pane_id: str | None
    window_id: str | None
    reason: str | None = None


@dataclass(frozen=True)
class TabRequest:
    working_directory: str
    command: tuple[str, ...]
    title: str


@dataclass(frozen=True)
class TabResult:
    succeeded: bool
    window_id: str | None
    reason: str | None = None


class SessionTerminal(Protocol):
    def window_for_session(self, session_id: SessionId) -> str | None: ...
    def current_window(self) -> str | None: ...
    def hosting_session(self, excluding_session_id: SessionId) -> SessionId | None: ...
    def open_tab(self, request: TabRequest) -> TabResult: ...


@dataclass(frozen=True)
class SessionPaneRequest:
    session_id: SessionId
    anchor_window_id: str
    activity_width_percent: int

    def __post_init__(self) -> None:
        if not 1 <= self.activity_width_percent <= 99:
            raise ValueError("activity pane width must be between 1 and 99 percent")


class SessionPaneControl(Protocol):
    def open_session_panes(self, request: SessionPaneRequest) -> TerminalResult: ...
    def open_pending_session_panes(self, request: SessionPaneRequest) -> TerminalResult: ...
    def adopt_pending_session_panes(
        self,
        pending_session_id: SessionId,
        session_id: SessionId,
    ) -> TerminalResult: ...
    def close_session_panes(self, session_id: SessionId) -> TerminalResult: ...


class SessionTabControl(Protocol):
    def paint_session_tab(
        self,
        session_id: SessionId,
        appearance: TabAppearance,
    ) -> TerminalResult: ...
    def clear_session_tab(self, session_id: SessionId) -> TerminalResult: ...


@dataclass(frozen=True)
class ResizeRequest:
    columns: int | None = None
    rows: int | None = None


@dataclass(frozen=True)
class ScreenText:
    text: str


class TerminalScreen(Protocol):
    def read_screen(
        self,
        window_id: str,
        ansi: bool = False,
    ) -> ScreenText | None: ...


@dataclass(frozen=True)
class TextSubmission:
    text: str
    mode: Literal["type", "paste"]


class TerminalControl(TerminalScreen, Protocol):
    def window_for_session(self, session_id: SessionId) -> str | None: ...
    def submit_text(self, window_id: str, submission: TextSubmission) -> TerminalResult: ...
    def send_key(self, window_id: str, key: str) -> TerminalResult: ...
    def close_tab(self, window_id: str) -> TerminalResult: ...
    def set_tab_title(self, window_id: str, title: str) -> TerminalResult: ...
    def open_tab(self, request: TabRequest) -> TabResult: ...


class TerminalFrontend(TerminalControl, Protocol):
    def usable(self) -> bool: ...
    def current_window(self) -> str | None: ...
    def window_for_session(self, session_id: SessionId) -> str | None: ...
    def tag_window(self, window_id: str, tags: Mapping[str, str]) -> TerminalResult: ...
    def set_tab_color(self, window_id: str, color: RGB) -> TerminalResult: ...
    def clear_tab_color(self, window_id: str) -> TerminalResult: ...
    def open_pane(self, request: PaneRequest) -> PaneResult: ...
    def close_pane(self, pane_id: str) -> TerminalResult: ...
    def resize_pane(self, pane_id: str, request: ResizeRequest) -> TerminalResult: ...
