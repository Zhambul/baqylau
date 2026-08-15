"""Codex session lifecycle through the shared harness contract."""

from contracts.harness import HarnessLifecycle, RecognizedSession, SessionLifecycleContext, SessionLifecycleRequest
from contracts.terminal import SessionPaneRequest

DEFAULT_ACTIVITY_WIDTH_PERCENT = 25


class CodexLifecycle(HarnessLifecycle):
    def apply(
        self,
        request: SessionLifecycleRequest,
        session: RecognizedSession,
        context: SessionLifecycleContext,
    ) -> None:
        if request.action == "finished":
            result = context.panes.close_session_panes(session.session_id)
            if not result.succeeded:
                raise RuntimeError(result.reason or "Codex session lifecycle failed")
            return
        if context.terminal.hosting_session(session.session_id) is not None:
            return
        anchor_window_id = (
            context.terminal.current_window()
            or context.terminal.window_for_session(session.session_id)
        )
        if anchor_window_id is None:
            return
        result = context.panes.open_session_panes(
            SessionPaneRequest(
                session.session_id,
                anchor_window_id,
                DEFAULT_ACTIVITY_WIDTH_PERCENT,
            )
        )
        if not result.succeeded:
            raise RuntimeError(result.reason or "Codex session lifecycle failed")


lifecycle = CodexLifecycle()
