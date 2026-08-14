"""Claude Code input-box reading through terminal mechanics."""

from __future__ import annotations

from contracts.harness import TerminalInputState
from contracts.terminal import TerminalScreen
from plugins.claude_code import suggestion


class ClaudeCodeTerminalProbe:
    def input_state(
        self,
        screen: TerminalScreen,
        window_id: str,
    ) -> TerminalInputState | None:
        screen_text = screen.read_screen(window_id, ansi=True)
        if screen_text is None:
            return None
        return TerminalInputState(
            typed_text=suggestion.typed(screen_text.text) or "",
            suggestion=suggestion.parse(screen_text.text),
        )
