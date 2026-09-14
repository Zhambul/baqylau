# Copyright (c) 2026 Zhambyl Yermagambet
"""Rename through the native dialog and confirm its saved event."""

import time
from pathlib import Path

from harness.contract import ControlHandler
from harness.impl.opencode2 import native_screen
from harness.impl.opencode2.records import NativeRecord
from harness.impl.opencode2.sources import OpenCodeSource
from harness.models import controls
from terminal.models.input import KeySendRequest, TextInputMode, TextSubmitRequest
from terminal.models.values import WindowId

CONFIRM_SECONDS = 5
READ_INTERVAL_SECONDS = 0.1


class RenameSessionHandler(ControlHandler):
    """Keep title changes out of the session prompt."""

    def __call__(
        self, request: controls.ControlRequest, control_context: controls.ControlContext,
    ) -> controls.ControlResult:
        """Submit a title only after the native rename dialog opens.

        Returns:
            Success only when a new native title event confirms the change.

        Raises:
            TypeError: If another control is dispatched here.

        """
        if not isinstance(request, controls.RenameSession):
            message = "rename_session requires RenameSession"
            raise TypeError(message)
        if control_context.terminal_window_id is None or not _valid_title(request.name):
            return controls.ControlResult(
                request.request_id, controls.ControlAcknowledgement.REJECTED,
                "rename needs a terminal and a non-empty single-line title",
            )
        position = _position(control_context.session.source_reference)
        if position is None:
            return controls.ControlResult(
                request.request_id, controls.ControlAcknowledgement.REJECTED, "native event log is unavailable",
            )
        reason = _submit(control_context, request.name)
        if reason:
            return controls.ControlResult(request.request_id, controls.ControlAcknowledgement.INDETERMINATE, reason)
        if _confirmed(control_context, position, request.name):
            return controls.ControlResult(request.request_id, controls.ControlAcknowledgement.ACKNOWLEDGED)
        return controls.ControlResult(
            request.request_id, controls.ControlAcknowledgement.INDETERMINATE, "native title was not confirmed",
        )


def _valid_title(title: str) -> bool:
    return (
        bool(title.strip())
        and title == title.strip()
        and all(character.isprintable() for character in title)
    )


def _position(source_reference: str) -> str | None:
    try:
        return str(max(Path(source_reference).stat().st_size - 1, 0))
    except OSError:
        return None


def _submit(control_context: controls.ControlContext, title: str) -> str | None:
    window_id = WindowId(str(control_context.terminal_window_id))
    opened = control_context.terminal.input.send_key(KeySendRequest(window_id, "ctrl+r"))
    if not opened.succeeded or not _dialog(control_context):
        return "native rename dialog did not open"
    cleared = control_context.terminal.input.send_key(KeySendRequest(window_id, "ctrl+u"))
    if not cleared.succeeded:
        return cleared.reason or "native title input could not be cleared"
    submitted = control_context.terminal.input.submit_text(TextSubmitRequest(window_id, title, TextInputMode.PASTE))
    return None if submitted.succeeded else submitted.reason or "native title was not submitted"


def _dialog(control_context: controls.ControlContext) -> bool:
    return native_screen.wait_for(
        control_context,
        lambda screen: "Rename session" in screen and "submit" in screen,
        CONFIRM_SECONDS,
    )


def _confirmed(control_context: controls.ControlContext, position: str, title: str) -> bool:
    source = OpenCodeSource(control_context.session.source_context)
    deadline = time.monotonic() + CONFIRM_SECONDS
    while time.monotonic() < deadline:
        records = source.read(position)
        if any(_matches(NativeRecord.model_validate_json(record.payload), title) for record in records):
            return True
        if records:
            position = records[-1].source_position or position
        time.sleep(READ_INTERVAL_SECONDS)
    return False


def _matches(native_record: NativeRecord, title: str) -> bool:
    return (
        native_record.session.parent_id is None
        and native_record.event.type == "session.renamed"
        and native_record.event.details.title == title
    )
