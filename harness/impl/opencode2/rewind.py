# Copyright (c) 2026 Zhambyl Yermagambet
"""Restore a named native message boundary and its editable prompt."""

from harness.contract import ControlHandler
from harness.impl.opencode2 import native_api, native_records
from harness.impl.opencode2.composer import ComposerError, OpenCodeComposer
from harness.impl.opencode2.records import NativeRecord
from harness.impl.opencode2.rewind_records import RevertRequest, RevertResponse
from harness.models import controls
from harness.services.terminal_driver import TerminalDriver

REWIND_ERRORS = (*native_api.ERRORS, ComposerError)


class ApplyRewindHandler(ControlHandler):
    """Restore one checked message without a model prompt."""

    def __call__(
        self, request: controls.ControlRequest, control_context: controls.ControlContext,
    ) -> controls.RewindResult:
        """Stage a rewind and restore the selected draft.

        Returns:
            The confirmed boundary and draft, or a failure.

        """
        if not isinstance(request, controls.ApplyRewind) or request.mode not in {"conversation", "both"}:
            return controls.RewindResult(request.request_id, controls.ControlAcknowledgement.REJECTED)
        if control_context.terminal_window_id is None or control_context.lead_active:
            return controls.RewindResult(request.request_id, controls.ControlAcknowledgement.REJECTED)
        record = _source(control_context, request)
        if record is None:
            return controls.RewindResult(
                request.request_id, controls.ControlAcknowledgement.REJECTED, "The native prompt was not found",
            )
        try:
            _restore(record, request, control_context)
        except REWIND_ERRORS as error:
            reason = str(error) if isinstance(error, ComposerError) else "The native rewind was not confirmed"
            return controls.RewindResult(
                request.request_id, controls.ControlAcknowledgement.INDETERMINATE, reason,
            )
        return controls.RewindResult(
            request.request_id, controls.ControlAcknowledgement.ACKNOWLEDGED, restored_text=request.target_text,
        )


def _restore(
    native_record: NativeRecord, request: controls.ApplyRewind, control_context: controls.ControlContext,
) -> None:
    if control_context.terminal_window_id is None:
        message = "Rewind requires a terminal"
        raise ComposerError(message)
    _stage(native_record, request)
    composer = OpenCodeComposer()
    driver = TerminalDriver(control_context.terminal)
    composer.clear(driver, control_context.terminal_window_id)
    composer.insert(driver, control_context.terminal_window_id, request.target_text)


def _stage(native_record: NativeRecord, request: controls.ApplyRewind) -> None:
    session_id = native_record.session.id
    body = RevertRequest(message_id=request.target_message_id, files=request.mode == "both")
    response = native_api.request(
        native_record, "POST", f"/api/session/{session_id}/revert/stage", body.model_dump_json(by_alias=True),
    )
    if RevertResponse.model_validate_json(response).boundary.message_id != request.target_message_id:
        message = "The native server restored another message"
        raise ValueError(message)


def _source(control_context: controls.ControlContext, request: controls.ApplyRewind) -> NativeRecord | None:
    latest = None
    found = False
    for record in native_records.read(control_context.session.source_context):
        if record.session.parent_id is not None:
            continue
        latest = record
        if (
            record.event.details.inbox_id == request.target_message_id
            and (record.prompt or "").strip() == request.target_text.strip()
        ):
            found = True
    return latest if found else None
