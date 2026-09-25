# Copyright (c) 2026 Zhambyl Yermagambet
"""Parse Claude system transcript records."""

from harness.impl.claude_code.canonical import records
from harness.impl.claude_code.canonical.transcript_commands import (
    command_text as _command_text,
    command_wrapper as _command_wrapper,
)
from harness.impl.claude_code.canonical.transcript_model_activity import GoalTranscriptRecord
from harness.impl.claude_code.canonical.transcript_model_commands import (
    SlashCommandTranscriptRecord,
)
from harness.impl.claude_code.canonical.transcript_model_core import (
    CompactTranscriptRecord,
    TextTranscriptRecord,
    TranscriptKind,
)
from harness.impl.claude_code.canonical.transcript_model_notifications import TranscriptRecord
from harness.impl.claude_code.canonical.transcript_user_text import strip_recap_hint as _strip_recap_hint


def _parse_system_record(line: str) -> TranscriptRecord | None:
    """Parse one native system record.

    Returns:
        The parsed record, or None for an unused subtype.

    """
    system_record = records.SystemRecord.model_validate_json(line)
    if system_record.subtype == "compact_boundary":
        return _compact_system_record(system_record)
    if system_record.subtype == "away_summary":
        return _away_summary_record(system_record)
    if system_record.subtype == "local_command" and isinstance(system_record.content, str):
        return _local_command_record(system_record.content)
    if isinstance(system_record.content, str):
        return _cleared_goal_record(system_record.content)
    return None


def _compact_system_record(system_record: records.SystemRecord) -> CompactTranscriptRecord:
    metadata = system_record.compact_metadata
    return CompactTranscriptRecord(metadata.pre_tokens if metadata else None)


def _away_summary_record(system_record: records.SystemRecord) -> TextTranscriptRecord | None:
    recap_text = _strip_recap_hint(system_record.content or "")
    return TextTranscriptRecord(recap_text, TranscriptKind.RECAP) if recap_text else None


def _local_command_record(content: str) -> SlashCommandTranscriptRecord | None:
    command_name, command_arguments = _command_wrapper(content)
    if not command_name:
        return None
    return SlashCommandTranscriptRecord(
        command_name,
        command_arguments,
        _command_text(content),
    )


def _cleared_goal_record(content: str) -> GoalTranscriptRecord | None:
    cleared_prefix = "Goal cleared:"
    if not content.startswith(cleared_prefix):
        return None
    objective = content[len(cleared_prefix) :].strip() or None
    return GoalTranscriptRecord(objective, "cleared", None)
