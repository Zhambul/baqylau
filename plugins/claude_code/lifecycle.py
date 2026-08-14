"""Claude Code session lifecycle through the shared harness contract."""

from contracts.harness import RecognizedSession, SessionLifecycleContext, SessionLifecycleRequest
from contracts.terminal import SessionPaneRequest
from plugins.claude_code import pane_settings
from plugins.claude_code.otel import launch as otel


class ClaudeCodeLifecycle:
    def apply(
        self,
        request: SessionLifecycleRequest,
        session: RecognizedSession,
        context: SessionLifecycleContext,
    ) -> None:
        if request.action == "finished":
            result = context.panes.close_session_panes(session.session_id)
        else:
            otel.start()
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
                    pane_settings.width_percent(session.working_directory or ""),
                )
            )
        if not result.succeeded:
            raise RuntimeError(result.reason or "Claude Code session lifecycle failed")


lifecycle = ClaudeCodeLifecycle()
