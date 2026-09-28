# Copyright (c) 2026 Zhambyl Yermagambet
"""Shared value coercion and canonical event construction for Claude Code translation."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from pydantic import BaseModel

from domain.content import Content, MediaType, StructuredContent, TextContent
from domain.references import ModelReference
from harness.impl.claude_code import model_names
from harness.models.raw_event_builders import CanonicalEventDraft as CanonicalEventDraft, canonical_event

if TYPE_CHECKING:
    from domain.event_base import CanonicalEvent, EventPayload
    from harness.models.raw_events import RawEvent

# The transcript's model field on a machine-injected assistant record. Not a
# model: nothing selected it and nothing runs on it.
SYNTHETIC_MODEL_ID = "<synthetic>"
ContentValue = str | float | bool | BaseModel | None


def model_reference(claude_code_model: str) -> ModelReference:
    """Return the model reference.

    Returns:
        Model reference.

    """
    return ModelReference(
        name=claude_code_model,
        display_name=model_names.short_model(claude_code_model),
    )


def timestamp(timestamp_source: str | float | None) -> float | None:
    """Return the timestamp.

    Returns:
        Timestamp.

    """
    if isinstance(timestamp_source, (int, float)):
        return float(timestamp_source)
    if not isinstance(timestamp_source, str) or not timestamp_source:
        return None
    try:
        return datetime.fromisoformat(timestamp_source).timestamp()
    except ValueError:
        return None


def content(
    content_source: ContentValue,
    *,
    markdown: bool = False,
) -> Content:
    """Return the content.

    Returns:
        Content.

    """
    if isinstance(content_source, BaseModel):
        return StructuredContent(content_source.model_dump_json(exclude_none=True))
    return TextContent(str(content_source or ""), MediaType.TEXT_MARKDOWN if markdown else MediaType.TEXT_PLAIN)


def event(
    raw_event: RawEvent,
    canonical_event_draft: CanonicalEventDraft,
) -> CanonicalEvent[EventPayload]:
    """Return the event.

    One fact. `turn_id` is for the two events that name a turn themselves;
        everything else is stamped with the open turn on its way out of the
        translator (`ClaudeCanonicalTranslator.translate`).

    Returns:
        Event.

    """
    return canonical_event(raw_event, canonical_event_draft)
