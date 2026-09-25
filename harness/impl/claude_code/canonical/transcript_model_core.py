# Copyright (c) 2026 Zhambyl Yermagambet
"""Define core Claude transcript record types."""

from dataclasses import dataclass
from enum import StrEnum

from harness.impl.claude_code.ids import ClaudeCodeCompactionId


class TranscriptKind(StrEnum):
    """Represent transcript kind."""

    BAD = "bad"
    COMPACT = "compact"
    COMPACT_SUMMARY = "compact_summary"
    RECAP = "recap"
    PROMPT = "prompt"
    SLASH_COMMAND = "slash_command"
    COMMAND_OUTPUT = "command_output"
    TEAM_MESSAGE = "teammsg"
    TOOL_RESULTS = "results"
    ASSISTANT = "assistant"
    MONITOR_EVENT = "monitor_event"
    MONITOR_ENDED = "monitor_ended"
    ACTOR_ASSIGNMENT_FINISHED = "actor_assignment_finished"
    BACKGROUND_COMMAND_COMPLETED = "background_command_completed"
    GOAL = "goal"


@dataclass(frozen=True)
class BadTranscriptRecord:
    """Represent bad transcript record."""

    raw: str
    kind: TranscriptKind = TranscriptKind.BAD


@dataclass(frozen=True)
class CompactTranscriptRecord:
    """Represent compact transcript record."""

    before_tokens: int | None
    kind: TranscriptKind = TranscriptKind.COMPACT


@dataclass(frozen=True)
class CompactSummaryTranscriptRecord:
    """Represent compact summary transcript record."""

    text: str
    boundary_id: ClaudeCodeCompactionId | None
    before_tokens: int | None = None
    kind: TranscriptKind = TranscriptKind.COMPACT_SUMMARY


@dataclass(frozen=True)
class TextTranscriptRecord:
    """Represent text transcript record."""

    text: str
    kind: TranscriptKind


@dataclass(frozen=True)
class PromptTranscriptRecord:
    """Represent prompt transcript record."""

    text: str
    meta: bool = False
    interrupted: bool = False
    queued: bool = False
    resumed: bool = False
    kind: TranscriptKind = TranscriptKind.PROMPT


@dataclass(frozen=True)
class SlashCommandTranscriptRecord:
    """Represent slash command transcript record."""

    name: str
    arguments: str
    text: str
    kind: TranscriptKind = TranscriptKind.SLASH_COMMAND


@dataclass(frozen=True)
class CommandOutputTranscriptRecord:
    """Represent local command output transcript record.

    A `/model` or `/effort` picker turn writes its result here, not in the
        command record: the command record carries no arguments.
    """

    text: str
    kind: TranscriptKind = TranscriptKind.COMMAND_OUTPUT
