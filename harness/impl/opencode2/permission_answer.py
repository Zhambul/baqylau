# Copyright (c) 2026 Zhambyl Yermagambet
"""Answer a specific native permission request and confirm its event."""

import time

from pydantic import BaseModel

from harness.impl.opencode2 import native_api, permissions
from harness.impl.opencode2.question_records import AnswerDraft
from harness.impl.opencode2.records import NativeRecord
from harness.impl.opencode2.sources import OpenCodeSource
from harness.models import controls

CONFIRM_SECONDS = 5
READ_INTERVAL_SECONDS = 0.1


class PermissionReply(BaseModel):
    """Encode the native permission decision."""

    reply: str


def submit(
    request: controls.AnswerQuestion, context: controls.ControlContext, answers: tuple[AnswerDraft, ...],
) -> controls.ControlResult:
    """Send one permission answer and wait for its saved decision.

    Returns:
        A confirmed result or an explicit failure.

    """
    reply = _reply(answers)
    open_code_source = OpenCodeSource(context.session.source_context)
    native_record, position = _pending(open_code_source, str(request.attention_id))
    if (
        reply is None or native_record is None
        or (reply == "always" and not native_record.event.details.save)
    ):
        return controls.ControlResult(
            request.request_id, controls.ControlAcknowledgement.REJECTED, "The native permission answer is not valid",
        )
    try:
        _send(native_record, request, reply)
    except native_api.ERRORS:
        return controls.ControlResult(
            request.request_id, controls.ControlAcknowledgement.INDETERMINATE, "The native permission request failed",
        )
    if _confirmed(open_code_source, position, str(request.attention_id), reply):
        return controls.ControlResult(request.request_id, controls.ControlAcknowledgement.ACKNOWLEDGED)
    return controls.ControlResult(
        request.request_id, controls.ControlAcknowledgement.INDETERMINATE,
        "The native permission answer was not observed",
    )


def _send(native_record: NativeRecord, request: controls.AnswerQuestion, reply: str) -> None:
    session_id = native_record.session.id
    path = f"/api/session/{session_id}/permission/{request.attention_id}/reply"
    native_api.request(native_record, "POST", path, PermissionReply(reply=reply).model_dump_json())


def _reply(answers: tuple[AnswerDraft, ...]) -> str | None:
    if len(answers) != 1:
        return None
    answer = answers[0]
    if answer.other or len(answer.selected) != 1:
        return None
    selected = answer.selected[0]
    for reply, label in permissions.CHOICES.items():
        if label == selected:
            return reply
    return None


def _pending(open_code_source: OpenCodeSource, identity: str) -> tuple[NativeRecord | None, str | None]:
    position = None
    pending_native_record = None
    while records := open_code_source.read(position):
        for raw in records:
            native_record = NativeRecord.model_validate_json(raw.payload)
            pending_native_record = _updated(native_record, identity, pending_native_record)
        position = records[-1].source_position
    return pending_native_record, position


def _updated(
    native_record: NativeRecord, identity: str, pending_native_record: NativeRecord | None,
) -> NativeRecord | None:
    if native_record.event.type == "permission.asked" and native_record.event.details.id == identity:
        return native_record
    if native_record.event.type == "permission.replied" and native_record.event.details.request_id == identity:
        return None
    return pending_native_record


def _confirmed(open_code_source: OpenCodeSource, position: str | None, identity: str, reply: str) -> bool:
    deadline = time.monotonic() + CONFIRM_SECONDS
    while time.monotonic() < deadline:
        records = open_code_source.read(position)
        for raw in records:
            native_record = NativeRecord.model_validate_json(raw.payload)
            if native_record.event.type == "permission.replied" and native_record.event.details.request_id == identity:
                return native_record.event.details.reply == reply
        if records:
            position = records[-1].source_position
        time.sleep(READ_INTERVAL_SECONDS)
    return False
