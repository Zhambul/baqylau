"""Shared value coercion and canonical event construction for Claude Code translation."""

from __future__ import annotations

import json
from datetime import datetime

from domain.events import CanonicalEvent, EventPayload
from domain.ids import ModelId, SelectionId, TurnId
from domain.values import Content, ModelReference, StructuredContent, TextContent
from harness.impl.claude_code import model
from harness.models import RawEvent, canonical_event


# The transcript's model field on a machine-injected assistant record. Not a
# model: nothing selected it and nothing runs on it.
SYNTHETIC_MODEL_ID = "<synthetic>"


def model_reference(native_id: ModelId) -> ModelReference:
    family = model.family(native_id)
    return ModelReference(
        native_id=native_id,
        display_name=model.short_model(native_id),
        selection_id=SelectionId(family) if family is not None else None,
    )


def timestamp(value: object) -> float | None:  # loose: claude code JSON, wave 2 gives it a real shape
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def content(
    value: object,  # loose: claude code JSON, wave 2 gives it a real shape
    *,
    markdown: bool = False,
) -> Content:
    if isinstance(value, (dict, list)):
        return StructuredContent(json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True))
    return TextContent(str(value or ""), "text/markdown" if markdown else "text/plain")


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
