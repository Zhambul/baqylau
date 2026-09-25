# Copyright (c) 2026 Zhambyl Yermagambet
"""Dispatch Claude Code collaboration records."""

from domain import event_base
from harness.impl.claude_code.canonical import message_models, transcript
from harness.impl.claude_code.canonical.message_collaboration_events import (
    assignment_finished_event,
    monitor_ended_event,
)
from harness.impl.claude_code.canonical.message_idle_events import team_message_events, teammate_idle_events


def translate_collaboration_record(
    source: message_models.TranscriptSource,
    record: transcript.TranscriptRecord,
    semantics: message_models.TranscriptSemantics,
) -> list[event_base.CanonicalEvent[event_base.EventPayload]] | None:
    """Translate collaboration completion, idle, and message records.

    Returns:
        The translated events, or None if this record is not supported here.

    """
    if isinstance(record, transcript.MonitorEndedTranscriptRecord):
        ended = monitor_ended_event(source, record, semantics.tool_calls)
        return [] if ended is None else [ended]
    if isinstance(record, transcript.ActorAssignmentFinishedTranscriptRecord):
        return [assignment_finished_event(source, record, semantics.tool_calls)]
    if isinstance(record, transcript.TeammateIdleTranscriptRecord):
        return teammate_idle_events(source, record, semantics.tool_calls)
    if isinstance(record, transcript.TeamMessageTranscriptRecord):
        return team_message_events(source, record, actor_started=semantics.actor_started)
    return None
