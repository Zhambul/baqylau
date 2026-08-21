"""What a harness's own TUI is showing right now, read off its screen."""

from __future__ import annotations

from dataclasses import dataclass

from domain.ids import WindowId


@dataclass(frozen=True)
class TerminalInputState:
    typed_text: str | None
    suggestion: str | None


@dataclass(frozen=True)
class TerminalSessionState:
    window_id: WindowId | None
    input_state: TerminalInputState | None
