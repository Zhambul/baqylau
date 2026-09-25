# Copyright (c) 2026 Zhambyl Yermagambet
"""Define Claude transcript notification record types."""

from dataclasses import dataclass

from harness.impl.claude_code.canonical import records
from harness.impl.claude_code.canonical.transcript_model_activity import (
    AssistantTranscriptRecord,
    BackgroundCommandCompletedTranscriptRecord,
    GoalTranscriptRecord,
    ResultsTranscriptRecord,
    TeammateIdleTranscriptRecord,
    TeamMessageTranscriptRecord,
)
from harness.impl.claude_code.canonical.transcript_model_commands import (
    CommandOutputTranscriptRecord,
    SlashCommandTranscriptRecord,
)
from harness.impl.claude_code.canonical.transcript_model_core import (
    BadTranscriptRecord,
    CompactSummaryTranscriptRecord,
    CompactTranscriptRecord,
    PromptTranscriptRecord,
    TextTranscriptRecord,
    TranscriptKind,
)
from harness.impl.claude_code.ids import ClaudeCodeActorId, ClaudeCodeCallId, ClaudeCodeShellId


@dataclass(frozen=True)
class MonitorEventTranscriptRecord:
    """Represent monitor event transcript record."""

    task: ClaudeCodeShellId
    summary: str
    event: str
    kind: TranscriptKind = TranscriptKind.MONITOR_EVENT


@dataclass(frozen=True)
class MonitorEndedTranscriptRecord:
    """Represent monitor ended transcript record."""

    task: ClaudeCodeShellId
    operation_id: ClaudeCodeCallId
    status: str
    kind: TranscriptKind = TranscriptKind.MONITOR_ENDED


@dataclass(frozen=True)
class ActorAssignmentFinishedTranscriptRecord:
    """Represent actor assignment finished transcript record."""

    assignment_id: ClaudeCodeCallId
    actor_id: ClaudeCodeActorId | None
    status: str
    summary: str
    result: str | None
    kind: TranscriptKind = TranscriptKind.ACTOR_ASSIGNMENT_FINISHED


@dataclass(frozen=True)
class SingleToolResult:
    """Join one tool response to its call identifier."""

    response: records.ToolResponse
    call_id: ClaudeCodeCallId


@dataclass(frozen=True)
class AncestryLine:
    """Describe one line in a transcript ancestry scan."""

    identity: str
    parent_identity: str | None
    is_prompt: bool


type TranscriptRecord = (
    BadTranscriptRecord
    | CompactTranscriptRecord
    | CompactSummaryTranscriptRecord
    | TextTranscriptRecord
    | PromptTranscriptRecord
    | SlashCommandTranscriptRecord
    | CommandOutputTranscriptRecord
    | TeamMessageTranscriptRecord
    | ResultsTranscriptRecord
    | TeammateIdleTranscriptRecord
    | AssistantTranscriptRecord
    | GoalTranscriptRecord
    | BackgroundCommandCompletedTranscriptRecord
    | MonitorEventTranscriptRecord
    | MonitorEndedTranscriptRecord
    | ActorAssignmentFinishedTranscriptRecord
)
