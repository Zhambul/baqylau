# Copyright (c) 2026 Zhambyl Yermagambet
"""Parse task notifications from Claude transcripts."""

import html
import re

from harness.impl.claude_code.canonical.transcript_model_activity import (
    BackgroundCommandCompletedTranscriptRecord,
)
from harness.impl.claude_code.canonical.transcript_model_notifications import (
    ActorAssignmentFinishedTranscriptRecord,
    MonitorEndedTranscriptRecord,
    MonitorEventTranscriptRecord,
    TranscriptRecord,
)
from harness.impl.claude_code.ids import ClaudeCodeActorId, ClaudeCodeCallId, ClaudeCodeShellId

TASK_NOTIFICATION = re.compile(r"<task-notification>(.*?)</task-notification>", re.DOTALL)
BACKGROUND_SUMMARY_PREFIX = "Background command"
MONITOR_SUMMARY_PREFIX = "Monitor"
MONITOR_EVENT_SUMMARY_PREFIX = "Monitor event:"
MONITOR_EXPIRED_MARKER = "[Monitor expired"
COMPLETED_STATUS = "completed"


def note_tag(document: str, name: str) -> str | None:
    """Return one tag value from a notification.

    Returns:
        The tag value, if it exists.

    """
    tag_match = re.search(rf"<{name}>(.*?)</{name}>", document, re.DOTALL)
    return tag_match.group(1).strip() if tag_match else None


def task_notification(content: str) -> TranscriptRecord:
    """Return the fact in one task notification.

    Returns:
        The parsed transcript record.

    """
    notification_match = TASK_NOTIFICATION.search(content)
    document = notification_match.group(1) if notification_match else content
    summary = note_tag(document, "summary") or ""
    if summary.startswith(BACKGROUND_SUMMARY_PREFIX):
        return background_notification(document)
    if _monitor_ended(summary, document):
        return monitor_ended_notification(document)
    event = note_tag(document, "event")
    if event is not None:
        return monitor_notification(document, summary, event)
    return assignment_notification(document, summary)


def _monitor_ended(summary: str, document: str) -> bool:
    """Return whether a task notification ends a monitor.

    A monitor's own end arrives in two shapes: a `Monitor "..." stream ended`
        summary, which may also carry the last event, and an expiry notice whose
        event text says the monitor expired. Only a `Monitor event:` summary is
        a progress event.

    Returns:
        Whether the notification ends a monitor.

    """
    if not summary.startswith(MONITOR_SUMMARY_PREFIX):
        return False
    if summary.startswith(MONITOR_EVENT_SUMMARY_PREFIX):
        return MONITOR_EXPIRED_MARKER in (note_tag(document, "event") or "")
    return True


def background_notification(document: str) -> BackgroundCommandCompletedTranscriptRecord:
    """Return a background-command completion.

    Returns:
        The parsed completion.

    """
    return BackgroundCommandCompletedTranscriptRecord(
        ClaudeCodeCallId(note_tag(document, "tool-use-id") or ""),
        note_tag(document, "status") or COMPLETED_STATUS,
        note_tag(document, "output-file"),
    )


def monitor_notification(document: str, summary: str, event: str) -> MonitorEventTranscriptRecord:
    """Return a monitor event.

    Returns:
        The parsed monitor event.

    """
    return MonitorEventTranscriptRecord(
        ClaudeCodeShellId(note_tag(document, "task-id") or ""),
        summary,
        event,
    )


def monitor_ended_notification(document: str) -> MonitorEndedTranscriptRecord:
    """Return a monitor completion.

    Returns:
        The parsed monitor completion.

    """
    return MonitorEndedTranscriptRecord(
        ClaudeCodeShellId(note_tag(document, "task-id") or ""),
        ClaudeCodeCallId(note_tag(document, "tool-use-id") or ""),
        note_tag(document, "status") or COMPLETED_STATUS,
    )


def assignment_notification(document: str, summary: str) -> ActorAssignmentFinishedTranscriptRecord:
    """Return an actor-assignment completion.

    Returns:
        The parsed assignment completion.

    """
    actor_id = note_tag(document, "task-id")
    return ActorAssignmentFinishedTranscriptRecord(
        ClaudeCodeCallId(note_tag(document, "tool-use-id") or ""),
        ClaudeCodeActorId(actor_id) if actor_id else None,
        note_tag(document, "status") or COMPLETED_STATUS,
        summary,
        html.unescape(note_tag(document, "result") or "") or None,
    )
