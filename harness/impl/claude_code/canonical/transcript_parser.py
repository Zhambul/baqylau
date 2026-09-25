# Copyright (c) 2026 Zhambyl Yermagambet
"""Dispatch Claude transcript records to their parsers."""

from pydantic import ValidationError

from harness.impl.claude_code.canonical import records
from harness.impl.claude_code.canonical.transcript_model_activity import AssistantTranscriptRecord
from harness.impl.claude_code.canonical.transcript_model_core import BadTranscriptRecord, TranscriptKind
from harness.impl.claude_code.canonical.transcript_model_notifications import TranscriptRecord
from harness.impl.claude_code.canonical.transcript_parse_other import (
    _parse_attachment_record,
    _parse_queue_record,
)
from harness.impl.claude_code.canonical.transcript_parse_system import _parse_system_record
from harness.impl.claude_code.canonical.transcript_parse_user import _parse_user_record

KINDS = (
    "bad",
    "compact",
    "compact_summary",
    "recap",
    TranscriptKind.PROMPT.value,
    TranscriptKind.SLASH_COMMAND.value,
    TranscriptKind.COMMAND_OUTPUT.value,
    TranscriptKind.TEAM_MESSAGE.value,
    "results",
    TranscriptKind.ASSISTANT.value,
    "monitor_event",
    "monitor_ended",
    "actor_assignment_finished",
    "background_command_completed",
    "goal",
)


def parse_line(line: str) -> TranscriptRecord | None:
    """One transcript JSONL line -> a typed record (see the module header).

    Dispatches on the raw `type` string FIRST — exactly as records.py's own
    header describes — and only then hands the line to the model that owns
    that type; an unrecognised `type` returns None here untouched, the same
    "ignored" outcome it always had. A recognised type that does not match its
    declared shape raises `pydantic.ValidationError`, which the interpreter
    loop turns into `translation_failed`.

    Returns:
        The transcript record.

    """
    try:
        record_type = records.TranscriptRecordHeader.model_validate_json(line).type
    except ValidationError:
        return _verified_record(BadTranscriptRecord(line))
    if record_type == "system":
        parsed_record = _parse_system_record(line)
    elif record_type == "user":
        parsed_record = _parse_user_record(line)
    else:
        parsed_record = _parse_other_line(line, record_type)
    return _verified_record(parsed_record)


def _verified_record(parsed_record: TranscriptRecord | None) -> TranscriptRecord | None:
    """Return a record only when its kind is in the declared vocabulary.

    Returns:
        The verified record.

    Raises:
        RuntimeError: If a parser returns an undeclared record kind.

    """
    if parsed_record is None or parsed_record.kind.value in KINDS:
        return parsed_record
    record_kind = parsed_record.kind.value
    message = f"undeclared Claude transcript record kind: {record_kind!r}"
    raise RuntimeError(message)


def _parse_other_line(line: str, record_type: str | None) -> TranscriptRecord | None:
    if record_type == TranscriptKind.ASSISTANT.value:
        assistant = records.AssistantRecord.model_validate_json(line)
        return AssistantTranscriptRecord(assistant.message)
    if record_type == "attachment":
        return _parse_attachment_record(line)
    if record_type == "queue-operation":
        return _parse_queue_record(line)
    return None
