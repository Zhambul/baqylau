# Copyright (c) 2026 Zhambyl Yermagambet
"""Define the Claude transcript records of slash commands and their output."""

from dataclasses import dataclass

from harness.impl.claude_code.canonical.transcript_model_core import TranscriptKind


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
