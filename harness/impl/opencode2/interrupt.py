# Copyright (c) 2026 Zhambyl Yermagambet
"""Confirm interruption from native records written after the request."""

import time
from pathlib import Path

from harness.contract import ControlHandler
from harness.impl.opencode2.records import NativeRecord
from harness.impl.opencode2.sources import OpenCodeSource
from harness.models import controls
from harness.models.raw_events import RawEvent
from terminal.models.input import KeySendRequest
from terminal.models.values import WindowId

CONFIRM_SECONDS = 1.5
READ_INTERVAL_SECONDS = 0.1


class InterruptHandler(ControlHandler):
    """Interrupt the owned native terminal and check its recorded outcome."""

    def __call__(
        self, request: controls.ControlRequest, control_context: controls.ControlContext,
    ) -> controls.InterruptResult:
        """Request interruption without closing the session.

        Returns:
            A confirmed interrupt, a rejected key write, or an uncertain result.

        """
        window_id = control_context.terminal_window_id
        if window_id is None:
            return controls.InterruptResult(
                request.request_id, controls.ControlAcknowledgement.REJECTED, "session has no terminal",
            )
        source = OpenCodeSource(control_context.session.source_context)
        position = _position(control_context.session.source_reference)
        for _ in range(2):
            sent = control_context.terminal.input.send_key(
                KeySendRequest(WindowId(str(window_id)), "escape"),
            )
            if not sent.succeeded:
                return controls.InterruptResult(
                    request.request_id, controls.ControlAcknowledgement.REJECTED, sent.reason,
                )
            if _observed(source, position):
                return controls.InterruptResult(
                    request.request_id, controls.ControlAcknowledgement.ACKNOWLEDGED, corroborated=True,
                )
        return controls.InterruptResult(
            request.request_id, controls.ControlAcknowledgement.INDETERMINATE, "native interruption was not observed",
        )


def _position(source_reference: str) -> str | None:
    size = Path(source_reference).stat().st_size
    return str(size - 1) if size else None


def _observed(open_code_source: OpenCodeSource, position: str | None) -> bool:
    deadline = time.monotonic() + CONFIRM_SECONDS
    while time.monotonic() < deadline:
        records = open_code_source.read(position)
        if any(_interrupted(record) for record in records):
            return True
        if records:
            position = records[-1].source_position
        time.sleep(READ_INTERVAL_SECONDS)
    return False


def _interrupted(raw_event: RawEvent) -> bool:
    record = NativeRecord.model_validate_json(raw_event.payload)
    return raw_event.parent_actor_id is None and record.event.type == "session.execution.interrupted"
