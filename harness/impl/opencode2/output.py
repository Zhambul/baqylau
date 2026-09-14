# Copyright (c) 2026 Zhambyl Yermagambet
"""Translate chunks from the common output-file reader."""

import base64

from pydantic import TypeAdapter

from domain import content, event_shell, outcomes, records
from harness.models import directives, raw_event_builders, raw_events


def translate(raw_event: raw_events.RawEvent) -> raw_events.TranslationResult:
    """Read one ordered output chunk.

    Returns:
        The canonical output update.

    """
    chunk = TypeAdapter(directives.ShellOutputChunk).validate_json(raw_event.payload)
    text = base64.b64decode(chunk.content_base64, validate=True).decode("utf-8", errors="replace")
    payload = event_shell.ShellProgressed(
        chunk.shell_id, chunk.ordinal, chunk.stream, content.TextContent(text), outcomes.OutputMode.APPEND,
    )
    return raw_events.TranslationResult((raw_event_builders.canonical_event(
        raw_event, raw_event_builders.CanonicalEventDraft(
            "shell", str(chunk.shell_id), f"output:{chunk.source_key}:{chunk.ordinal}", payload,
        ),
    ),), records.RecordedTranslationDecision.TRANSLATED)
