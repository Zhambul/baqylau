# Copyright (c) 2026 Zhambyl Yermagambet
"""Compact the session through the native command a person would type."""

from harness.contract import ControlHandler
from harness.impl.opencode2 import compaction, native_commands, native_records
from harness.impl.opencode2.records import NativeRecord
from harness.models import controls

CONFIRM_SECONDS = 10


class CompactHandler(ControlHandler):
    """Request native compaction and confirm that it started."""

    def __call__(
        self, request: controls.ControlRequest, control_context: controls.ControlContext,
    ) -> controls.ControlResult:
        """Submit the native compact command and wait for its first record.

        Returns:
            An acknowledged compaction, a rejected request, or an uncertain one.

        Raises:
            TypeError: If another control is dispatched here.

        """
        if not isinstance(request, controls.Compact):
            message = "compact requires Compact"
            raise TypeError(message)
        position = native_records.position(control_context.session.source_reference)
        reason = native_commands.submit(control_context, native_commands.COMPACT)
        if reason is not None:
            return controls.ControlResult(
                request.request_id, controls.ControlAcknowledgement.REJECTED, reason,
            )
        if native_records.observed(
            control_context.session.source_context, position, _started, CONFIRM_SECONDS,
        ):
            return controls.CommandResult(request.request_id, controls.ControlAcknowledgement.ACKNOWLEDGED)
        return controls.CommandResult(
            request.request_id,
            controls.ControlAcknowledgement.INDETERMINATE,
            "native compaction did not start",
        )


def _started(native_record: NativeRecord) -> bool:
    return native_record.event.type == compaction.STARTED
