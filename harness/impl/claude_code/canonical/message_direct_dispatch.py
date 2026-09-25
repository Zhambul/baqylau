# Copyright (c) 2026 Zhambyl Yermagambet
"""Dispatch direct Claude Code transcript records."""

from domain import event_base
from harness.impl.claude_code.canonical import message_models, transcript
from harness.impl.claude_code.canonical.message_collaboration_events import (
    background_command_events,
    goal_event,
    monitor_event,
)
from harness.impl.claude_code.canonical.message_commands import command_output, slash_command
from harness.impl.claude_code.canonical.message_prompts import translate_prompt


def translate_direct_record(
    source: message_models.TranscriptSource,
    record: transcript.TranscriptRecord,
    semantics: message_models.TranscriptSemantics,
) -> list[event_base.CanonicalEvent[event_base.EventPayload]] | None:
    """Translate a prompt, slash command, or direct system record.

    Returns:
        The translated events, or None if this record is not supported here.

    """
    if isinstance(record, transcript.PromptTranscriptRecord):
        return translate_prompt(source, record, semantics)
    if isinstance(record, transcript.SlashCommandTranscriptRecord):
        return slash_command(source, record, semantics)
    if isinstance(record, transcript.CommandOutputTranscriptRecord):
        return command_output(source, record, semantics)
    return translate_direct_system_record(source, record, semantics)


def translate_direct_system_record(
    source: message_models.TranscriptSource,
    record: transcript.TranscriptRecord,
    semantics: message_models.TranscriptSemantics,
) -> list[event_base.CanonicalEvent[event_base.EventPayload]] | None:
    """Translate goal and background shell system records.

    Returns:
        The translated events, or None if this record is not supported here.

    """
    if isinstance(record, transcript.GoalTranscriptRecord):
        return [goal_event(source, record)]
    if isinstance(record, transcript.BackgroundCommandCompletedTranscriptRecord):
        return background_command_events(source, record)
    if isinstance(record, transcript.MonitorEventTranscriptRecord):
        return monitor_event(source, record, semantics.tool_calls)
    return None
