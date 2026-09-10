# Copyright (c) 2026 Zhambyl Yermagambet
"""Parse Claude user transcript records."""

from harness.impl.claude_code.canonical import records
from harness.impl.claude_code.canonical.transcript_commands import (
    command_caveat as _command_caveat,
    command_standard_output as _command_standard_output,
    command_text as _command_text,
    command_wrapper as _command_wrapper,
)
from harness.impl.claude_code.canonical.transcript_model_activity import (
    ResultsTranscriptRecord,
    TeammateIdleTranscriptRecord,
    TeamMessageTranscriptRecord,
)
from harness.impl.claude_code.canonical.transcript_model_core import (
    CompactSummaryTranscriptRecord,
    PromptTranscriptRecord,
    SlashCommandTranscriptRecord,
    TranscriptKind,
)
from harness.impl.claude_code.canonical.transcript_model_notifications import (
    MonitorEndedTranscriptRecord,
    MonitorEventTranscriptRecord,
    TranscriptRecord,
)
from harness.impl.claude_code.canonical.transcript_notifications import task_notification as _task_notification
from harness.impl.claude_code.canonical.transcript_user_text import (
    classify_user_text,
    injected as _injected,
    interrupted as _interrupted,
    resumes_turn as _resumes_turn,
    teammate_idle_notifications,
)
from harness.impl.claude_code.ids import ClaudeCodeCompactionId


def _parse_user_text(user_record: records.UserRecord, content: str) -> TranscriptRecord | None:
    """Parse plain text from one native user record.

    Returns:
        The parsed text record, or None for native control text.

    """
    if not content.strip():
        return None
    if user_record.is_compact_summary:
        compaction_id = None if user_record.parent_uuid is None else ClaudeCodeCompactionId(user_record.parent_uuid)
        return CompactSummaryTranscriptRecord(content, compaction_id)
    if user_record.origin is not None and user_record.origin.kind == "task-notification":
        return _user_task_notification(content)
    classified_record = _team_or_idle_record(content)
    if classified_record is not None:
        return classified_record
    return _ordinary_user_text(user_record, content)


def _user_task_notification(content: str) -> TranscriptRecord | None:
    notification = _task_notification(content)
    if isinstance(notification, (MonitorEventTranscriptRecord, MonitorEndedTranscriptRecord)):
        return None
    return notification


def _team_or_idle_record(content: str) -> TranscriptRecord | None:
    idle_notifications = teammate_idle_notifications(content)
    if idle_notifications:
        return TeammateIdleTranscriptRecord(idle_notifications)
    kind, sender_id, message_body = classify_user_text(content)
    if kind == TranscriptKind.TEAM_MESSAGE.value:
        return TeamMessageTranscriptRecord(sender_id, message_body or "")
    return None


def _ordinary_user_text(
    user_record: records.UserRecord,
    content: str,
) -> TranscriptRecord | None:
    command_name, command_arguments = _command_wrapper(content)
    if command_name:
        return SlashCommandTranscriptRecord(command_name, command_arguments, _command_text(content))
    if _command_caveat(content) or _command_standard_output(content):
        return None
    injected = _injected(user_record, content)
    return PromptTranscriptRecord(
        content,
        injected,
        _interrupted(user_record, content),
        user_record.prompt_source == "queued",
        injected and _resumes_turn(content),
    )


def _parse_user_results(
    user_record: records.UserRecord,
    content: list[records.MessageContentBlock],
) -> ResultsTranscriptRecord | None:
    """Parse tool results from one native user record.

    Returns:
        The parsed results record, or None for empty content.

    """
    result_blocks = [content_block for content_block in content if isinstance(content_block, records.ToolResultBlock)]
    text_blocks = [
        content_block.text or ""
        for content_block in content
        if isinstance(content_block, records.TextBlock) and (content_block.text or "").strip()
    ]
    if not result_blocks and not text_blocks:
        return None
    first_text = text_blocks[0] if text_blocks else ""
    return ResultsTranscriptRecord(
        tuple(result_blocks),
        user_record.tool_use_result,
        tuple(text_blocks),
        _injected(user_record, first_text),
        user_record.tool_denial_kind == "user-rejected",
        _interrupted(user_record, first_text),
    )


def _parse_user_record(line: str) -> TranscriptRecord | None:
    """Parse one native user record.

    Returns:
        The parsed record, or None for empty content.

    """
    user_record = records.UserRecord.model_validate_json(line)
    content = user_record.message.content if user_record.message else None
    if isinstance(content, str):
        return _parse_user_text(user_record, content)
    if isinstance(content, list):
        return _parse_user_results(user_record, content)
    return None
