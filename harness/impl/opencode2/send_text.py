# Copyright (c) 2026 Zhambyl Yermagambet
"""Send a prompt through the native OpenCode input."""

from harness.contract import ControlHandler
from harness.impl.opencode2 import attachments, native_records
from harness.impl.opencode2.composer import ComposerError, OpenCodeComposer
from harness.impl.opencode2.records import NativeRecord
from harness.models import controls
from harness.services.terminal_driver import TerminalDriver
from terminal.models.input import TextInputMode, TextSubmitRequest
from terminal.models.values import WindowId

DELIVERED = "session.inbox.delivered"
NATIVE_COMMAND = "/"
# OpenCode2 puts every prompt in the inbox of its session. An idle session takes
# it at once; a session that runs keeps it there until its turn ends. A prompt
# that is not taken inside this time is therefore a QUEUED prompt.
DELIVERY_SECONDS = 3


class SendTextHandler(ControlHandler):
    """Deliver a prompt to the session's owned terminal."""

    def __call__(
        self, request: controls.ControlRequest, control_context: controls.ControlContext,
    ) -> controls.ControlOutcome:
        """Submit the text through bracketed paste.

        Returns:
            The terminal delivery result.

        Raises:
            TypeError: If another control is dispatched here.

        """
        if not isinstance(request, controls.SendText):
            message = "send_text requires SendText"
            raise TypeError(message)
        if control_context.terminal_window_id is None:
            return controls.ControlResult(
                request.request_id, controls.ControlAcknowledgement.REJECTED,
                "OpenCode2 text delivery needs a terminal",
            )
        try:
            OpenCodeComposer().clear(TerminalDriver(control_context.terminal), control_context.terminal_window_id)
        except ComposerError as error:
            return controls.ControlResult(request.request_id, controls.ControlAcknowledgement.REJECTED, str(error))
        position = native_records.position(control_context.session.source_reference)
        result = control_context.terminal.input.submit_text(TextSubmitRequest(
            WindowId(str(control_context.terminal_window_id)),
            attachments.prompt(request.text, request.attachments), TextInputMode.PASTE,
        ))
        if not result.succeeded:
            return controls.ControlResult(
                request.request_id, controls.ControlAcknowledgement.REJECTED, result.reason,
            )
        return controls.MessageDeliveryResult(request.request_id, _status(control_context, request, position))


def _status(
    control_context: controls.ControlContext,
    request: controls.SendText,
    position: str | None,
) -> controls.MessageDeliveryStatus:
    if request.text.startswith(NATIVE_COMMAND):
        # A native command is not a prompt. The native side runs it itself and
        # does not put it in the inbox of the session.
        return controls.MessageDeliveryStatus.SENT
    taken = native_records.observed(
        control_context.session.source_context,
        position,
        lambda native_record: _taken(native_record, request.text),
        DELIVERY_SECONDS,
    )
    return controls.MessageDeliveryStatus.SENT if taken else controls.MessageDeliveryStatus.QUEUED


def _taken(native_record: NativeRecord, text: str) -> bool:
    if native_record.event.type != DELIVERED:
        return False
    delivered = native_record.prompt or ""
    return delivered.strip() == text.strip()
