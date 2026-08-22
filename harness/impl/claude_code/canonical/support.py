"""Shared value coercion and canonical event construction for Claude Code translation."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from domain.events import CanonicalEvent, EventPayload
from domain.ids import TurnId
from domain.values import Content, MediaType, ModelReference, StructuredContent, TextContent
from harness.impl.claude_code import model
from harness.models import RawEvent, canonical_event


# The transcript's model field on a machine-injected assistant record. Not a
# model: nothing selected it and nothing runs on it.
SYNTHETIC_MODEL_ID = "<synthetic>"


def model_reference(claude_code_model: model.ClaudeCodeModel) -> ModelReference:
    return ModelReference(
        name=claude_code_model,
        display_name=model.short_model(claude_code_model),
    )


def timestamp(value: str | int | float | None) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def content(
    value: str | int | float | bool | BaseModel | None,
    *,
    markdown: bool = False,
) -> Content:
    if isinstance(value, BaseModel):
        return StructuredContent(value.model_dump_json(exclude_none=True))
    return TextContent(str(value or ""), MediaType.TEXT_MARKDOWN if markdown else MediaType.TEXT_PLAIN)


def event(
    raw_event: RawEvent,
    subject_type: str,
    subject_id: str,
    phase: str,
    event_payload: EventPayload,
    *,
    turn_id: TurnId | None = None,
    occurred_at: float | None = None,
) -> CanonicalEvent[EventPayload]:
    """One fact. `turn_id` is for the two events that name a turn themselves;
    everything else is stamped with the open turn on its way out of the
    translator (`ClaudeCanonicalTranslator.translate`)."""
    return canonical_event(
        raw_event, subject_type, subject_id, phase, event_payload,
        turn_id=turn_id, occurred_at=occurred_at,
    )
