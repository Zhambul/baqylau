"""Claude Code input-box reading through terminal mechanics."""

from __future__ import annotations

from domain.ids import WindowId
from harness.contract import HarnessTerminalProbe
from harness.models import TerminalInputState
from harness.impl.claude_code import suggestion
from terminal.contract import TerminalViewport
from terminal.models import ScreenReadRequest
from terminal.models.values import WindowId as NativeWindowId


class ClaudeCodeTerminalProbe(HarnessTerminalProbe):
    def input_state(
        self,
        terminal_viewport: TerminalViewport,
        window_id: WindowId,
    ) -> TerminalInputState | None:
        screen = terminal_viewport.read_screen(
            ScreenReadRequest(NativeWindowId(str(window_id)), ansi=True)
        )
        if screen.text is None:
            return None
        return TerminalInputState(
            typed_text=suggestion.typed(screen.text) or "",
            suggestion=suggestion.parse(screen.text),
        )
