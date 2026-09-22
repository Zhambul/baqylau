# Copyright (c) 2026 Zhambyl Yermagambet
"""Split SDK client implementation."""

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import urlencode

from pydantic import ValidationError

from sdk import application_models, sse, transport, wait_states
from sdk.client_adapters import (
    DIAGNOSTICS_CHECKPOINT,
    DIAGNOSTICS_REPORT,
    GLOBAL_STREAM,
    PANE_COMMAND,
    RAW_EVENT_AUDIT,
    SESSION_STREAM,
    TERMINAL_DIAGNOSTICS,
)
from sdk.client_feed import (
    _stream_cursor,
    _stream_error_reason,
)
from sdk.client_models import (
    GlobalStreamUpdate,
    SessionRef,
    SessionStreamUpdate,
)
from sdk.client_wait import wait_for

if TYPE_CHECKING:
    from sdk.client_application import ApplicationResource


class UsageResource:
    """Represent usage resource."""

    def __init__(self, application: ApplicationResource) -> None:
        """Initialize the usage resource."""
        self.application = application

    def state(self) -> application_models.global_application_response.GlobalApplicationResponse:
        """Return the state.

        Returns:
            State.

        """
        return self.application.state()


class TerminalResource:
    """Named terminal gestures with the complete typed command verdict."""

    def __init__(self, transport: transport.HttpTransport) -> None:
        """Initialize the terminal resource."""
        self.transport = transport

    def toggle_panes(
        self, *, window_id: str, workspace: str,
    ) -> application_models.pane_command_response.PaneCommandResponse:
        """Toggle panes.

        Returns:
            The pane command response.

        """
        return self._gesture(
            "toggle",
            application_models.toggle_request.TogglePanesRequest(window_id=window_id, working_directory=workspace),
        )

    def grow_activity_pane(
        self,
        *,
        window_id: str,
        workspace: str,
        columns: int | None = None,
    ) -> application_models.pane_command_response.PaneCommandResponse:
        """Grow activity pane.

        Returns:
            The pane command response.

        """
        return self._gesture(
            "grow",
            application_models.grow_request.GrowPaneRequest(
                window_id=window_id,
                working_directory=workspace,
                columns=columns,
            ),
        )

    def shrink_activity_pane(
        self,
        *,
        window_id: str,
        workspace: str,
        columns: int | None = None,
    ) -> application_models.pane_command_response.PaneCommandResponse:
        """Shrink activity pane.

        Returns:
            The pane command response.

        """
        return self._gesture(
            "shrink",
            application_models.shrink_request.ShrinkPaneRequest(
                window_id=window_id,
                working_directory=workspace,
                columns=columns,
            ),
        )

    def reset_activity_pane(
        self, *, window_id: str, workspace: str,
    ) -> application_models.pane_command_response.PaneCommandResponse:
        """Reset activity pane.

        Returns:
            The pane command response.

        """
        return self._gesture(
            "reset",
            application_models.reset_request.ResetPaneRequest(window_id=window_id, working_directory=workspace),
        )

    def set_activity_pane_width(
        self,
        *,
        window_id: str,
        workspace: str,
        percent: int,
    ) -> application_models.pane_command_response.PaneCommandResponse:
        """Set activity pane width.

        Returns:
            The pane command response.

        """
        return self._gesture(
            "set-percent",
            application_models.set_percent_request.SetPanePercentRequest(
                window_id=window_id,
                working_directory=workspace,
                percent=percent,
            ),
        )

    def _gesture(
        self,
        command: str,
        document: application_models.pane_gesture_request.PaneGestureRequest,
    ) -> application_models.pane_command_response.PaneCommandResponse:
        _status, response = self.transport.post(
            f"/api/terminal/panes/{command}",
            document,
            PANE_COMMAND,
            {200, 409},
        )
        return response


class StreamsResource:
    """Represent streams resource."""

    def __init__(self, transport: transport.HttpTransport) -> None:
        """Initialize the streams resource."""
        self.transport = transport

    def next_session_update(
        self,
        session: SessionRef,
        *,
        after_cursor: int,
        last_event_id: int | None = None,
    ) -> SessionStreamUpdate:
        """Return the next session update.

        Returns:
            Next session update.

        Raises:
            ApiFailureError: If the API request fails.

        """
        session_id = session.path_segment
        raw = self._next(
            f"/sessionData/{session_id}/stream?after_cursor={after_cursor}",
            last_event_id=last_event_id,
        )
        try:
            frame = SESSION_STREAM.validate_json(raw.payload)
        except ValidationError as error:
            msg = f"session stream returned an invalid frame: {error}"
            raise transport.ApiFailureError(msg) from error
        return SessionStreamUpdate(_stream_cursor(raw), frame)

    def next_global_update(
        self,
        *,
        after_cursor: int,
        last_event_id: int | None = None,
    ) -> GlobalStreamUpdate:
        """Return the next global update.

        Returns:
            Next global update.

        Raises:
            ApiFailureError: If the API request fails.

        """
        raw = self._next(
            f"/sessionData/stream?after_cursor={after_cursor}",
            last_event_id=last_event_id,
        )
        try:
            frame = GLOBAL_STREAM.validate_json(raw.payload)
        except ValidationError as error:
            msg = f"global stream returned an invalid frame: {error}"
            raise transport.ApiFailureError(msg) from error
        return GlobalStreamUpdate(_stream_cursor(raw), frame)

    def _next(
        self,
        path: str,
        *,
        last_event_id: int | None,
    ) -> sse.SseEvent:
        headers = None if last_event_id is None else {"Last-Event-ID": str(last_event_id)}
        with self.transport.event_stream(path, headers=headers) as lines:
            for sse_event in sse.events(lines):
                if sse_event.event == "error":
                    reason = _stream_error_reason(path, sse_event)
                    msg = f"GET {path} stream failed: {reason}"
                    raise transport.ApiFailureError(msg)
                if sse_event.event == "sessionData":
                    return sse_event
        msg = f"GET {path} ended before a sessionData frame"
        raise transport.ApiFailureError(msg)


class DiagnosticsResource:
    """Represent diagnostics resource."""

    def __init__(self, transport: transport.HttpTransport) -> None:
        """Initialize the diagnostics resource."""
        self.transport = transport

    def checkpoint(self) -> application_models.models.DiagnosticsCheckpointResponse:
        """Return the checkpoint.

        Returns:
            Checkpoint.

        """
        return self.transport.get("/api/diagnostics/checkpoint", DIAGNOSTICS_CHECKPOINT)

    def terminal(self) -> application_models.models.TerminalDiagnosticsResponse:
        """Return the terminal.

        Returns:
            Terminal.

        """
        return self.transport.get("/api/diagnostics/terminal", TERMINAL_DIAGNOSTICS)

    def report(
        self,
        start: application_models.models.DiagnosticsCheckpointResponse,
        end: application_models.models.DiagnosticsCheckpointResponse,
    ) -> application_models.models.DiagnosticsReportResponse:
        """Report.

        Returns:
            The diagnostics report response.

        """
        query = urlencode(
            {
                "after_raw_event": start.raw_event_cursor,
                "through_raw_event": end.raw_event_cursor,
                "after_audit_error": start.audit_error_cursor,
                "through_audit_error": end.audit_error_cursor,
            },
        )
        return self.transport.get(f"/api/diagnostics/report?{query}", DIAGNOSTICS_REPORT)

    def raw_event_audit(self, raw_event_id: str) -> application_models.raw_event_audit_models.RawEventAuditResponse:
        """Return the bounded audit of one raw event.

        Returns:
            The bounded raw-event audit response.

        """
        return self.transport.get(f"/api/diagnostics/raw-events/{raw_event_id}", RAW_EVENT_AUDIT)

    def wait_until_drained(self, timeout: float = 30.0) -> application_models.models.DiagnosticsCheckpointResponse:
        """Wait until drained.

        Returns:
            The diagnostics checkpoint response.

        """
        drain_progress = wait_states.DrainProgress()
        return wait_for(
            "the event pipeline to drain",
            lambda: self._read_drain_checkpoint(drain_progress),
            timeout=timeout,
        )

    def _read_drain_checkpoint(
        self,
        drain_progress: wait_states.DrainProgress,
    ) -> application_models.models.DiagnosticsCheckpointResponse | None:
        checkpoint = self.checkpoint()
        return checkpoint if drain_progress.observe(checkpoint) else None
