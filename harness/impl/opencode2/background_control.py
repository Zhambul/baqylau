# Copyright (c) 2026 Zhambyl Yermagambet
"""Move an active native shell to the background and confirm its event."""

from functools import partial

from harness.contract import ControlHandler
from harness.impl.opencode2 import native_api, native_records
from harness.impl.opencode2.records import NativeRecord
from harness.models import controls

CONFIRM_SECONDS = 5


class BackgroundHandler(ControlHandler):
    """Use the background control of the session's native server."""

    def __call__(
        self, request: controls.ControlRequest, control_context: controls.ControlContext,
    ) -> controls.ControlResult:
        """Move foreground work after the native session reports an active shell.

        Returns:
            A confirmed result or an explicit failure.

        Raises:
            TypeError: If another control is dispatched here.

        """
        if not isinstance(request, controls.Background):
            message = "background requires Background"
            raise TypeError(message)
        latest, active = _pending(control_context)
        if latest is None or not active or not control_context.lead_active:
            return controls.ControlResult(
                request.request_id, controls.ControlAcknowledgement.REJECTED, "No foreground shell is active",
            )
        position = native_records.position(control_context.session.source_reference)
        session_id = latest.session.id
        try:
            native_api.request(latest, "POST", f"/api/session/{session_id}/background", "{}")
        except native_api.ERRORS:
            return controls.ControlResult(
                request.request_id, controls.ControlAcknowledgement.INDETERMINATE,
                "The native background request failed",
            )
        if native_records.observed(
            control_context.session.source_context, position,
            partial(_confirmed, active=active), CONFIRM_SECONDS,
        ):
            return controls.ControlResult(request.request_id, controls.ControlAcknowledgement.ACKNOWLEDGED)
        return controls.ControlResult(
            request.request_id, controls.ControlAcknowledgement.INDETERMINATE,
            "The native shell did not report a background transition",
        )


def _pending(context: controls.ControlContext) -> tuple[NativeRecord | None, frozenset[str]]:
    latest = None
    active: set[str] = set()
    for native_record in native_records.read(context.session.source_context):
        if native_record.session.parent_id is not None:
            continue
        latest = native_record
        _track(native_record, active)
    return latest, frozenset(active)


def _track(native_record: NativeRecord, active: set[str]) -> None:
    identity = native_record.event.details.id
    if identity is None:
        return
    if _foreground(native_record):
        active.add(identity)
    if native_record.event.type in {"session.tool.success", "session.tool.failed"}:
        active.discard(identity)


def _foreground(native_record: NativeRecord) -> bool:
    tool = native_record.tool
    if tool is None or tool.input is None:
        return False
    return (
        native_record.event.type == "session.tool.called"
        and tool.name == "shell" and not tool.input.background
    )


def _confirmed(native_record: NativeRecord, *, active: frozenset[str]) -> bool:
    metadata = native_record.event.details.metadata
    if metadata is None:
        return False
    return (
        native_record.session.parent_id is None
        and native_record.event.type == "session.tool.success"
        and native_record.event.details.id in active
        and metadata.status == "running"
    )
