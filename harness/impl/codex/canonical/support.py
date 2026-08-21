"""Shared value coercion and canonical event construction for Codex translation."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from domain.events import CanonicalEvent, EventPayload
from domain.ids import ModelId, SelectionId, TurnId
from domain.values import Content, ModelReference, Outcome, StructuredContent, TextContent
from harness.models import RawEvent, canonical_event


def model_reference(native_id: ModelId) -> ModelReference:
    return ModelReference(native_id, native_id, SelectionId(native_id))


def timestamp(value: object) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def exit_code(record: dict[str, Any]) -> int | None:
    """The record's exit status, honest about zero: `0` is a real exit code
    (a falsy-int coercion once turned a clean exit into outcome "failed")."""
    # Parsed from the same string the guard tests, rather than from the raw
    # value: the two were separate expressions, so nothing connected "this
    # renders as digits" to "this converts to an int".
    text = str(record.get("exit"))
    return int(text) if text.lstrip("-").isdigit() else None


def content(value: object, *, markdown: bool = False) -> Content:
    if isinstance(value, (dict, list)):
        return StructuredContent(json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True))
    return TextContent(str(value or ""), "text/markdown" if markdown else "text/plain")


def event(
    raw_event: RawEvent,
    subject_type: str,
    subject_id: str,
    phase: str,
    event_payload: EventPayload,
    turn_id: TurnId | None = None,
    occurred_at: float | None = None,
) -> CanonicalEvent[EventPayload]:
    return canonical_event(
        raw_event, subject_type, subject_id, phase, event_payload,
        turn_id=turn_id, occurred_at=occurred_at,
    )


def outcome_of(succeeded: bool) -> Outcome:
    return "succeeded" if succeeded else "failed"
