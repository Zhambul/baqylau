"""Find native Codex resume commands in terminal windows."""

from __future__ import annotations

from domain.ids import SessionId, WindowId
from harness.contract import HarnessResumeLocator
from harness.impl.codex.ids import CodexSessionId, session_id_from_codex
from terminal.models import WindowInfo


class CodexResumeLocator(HarnessResumeLocator):
    def locate(
        self,
        windows: tuple[WindowInfo, ...],
    ) -> tuple[tuple[SessionId, WindowId], ...]:
        located: list[tuple[SessionId, WindowId]] = []
        for window in windows:
            for process in window.processes:
                native_session_id = _resumed_session(process.command)
                if native_session_id is not None:
                    match = (
                        session_id_from_codex(native_session_id),
                        WindowId(str(window.window_id)),
                    )
                    if all(existing[0] != match[0] for existing in located):
                        located.append(match)
        return tuple(located)


def _resumed_session(command: tuple[str, ...]) -> CodexSessionId | None:
    try:
        resume_index = command.index("resume")
        value = command[resume_index + 1].strip()
    except (ValueError, IndexError):
        return None
    if not value or value.startswith("-"):
        return None
    return CodexSessionId(value)
