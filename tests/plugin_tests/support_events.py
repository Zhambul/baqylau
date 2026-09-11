# Copyright (c) 2026 Zhambyl Yermagambet
"""Shared fixtures and builders for canonical harness tests."""

from __future__ import annotations

import json
import typing

from domain import event_base, ids as domain_ids
from harness.models import raw_events as raw_event_models
from repository.mapper import facts as mapper
from tests.harness_names import CLAUDE_CODE_HARNESS
from tests.plugin_tests import vocabulary as fixture

if typing.TYPE_CHECKING:
    from repository.model.facts import CanonicalEventInsertRow
    from tests.plugin_tests.support_values import JsonValue


class RawEventArguments(typing.TypedDict):
    """Contain raw event fixture fields."""

    harness: domain_ids.HarnessName
    source_type: str
    raw_event_id: str
    source_position: typing.NotRequired[str]
    observed_at: typing.NotRequired[float]


def encoded_event(event: event_base.CanonicalEvent[event_base.EventPayload]) -> CanonicalEventInsertRow:
    """Convert an event to its stored comparison form.

    Returns:
        The canonical insert row with acceptance time set to zero.

    """
    return mapper.canonical_event_insert_row(event, 0)


def raw_event(
    document: JsonValue,
    **arguments: typing.Unpack[RawEventArguments],
) -> raw_event_models.RawEvent:
    """Encode a test document as a raw harness event.

    Returns:
        The event with the supplied source fields and fixture session identity.

    """
    return raw_event_models.RawEvent(
        raw_event_id=domain_ids.RawEventId(arguments["raw_event_id"]),
        harness=arguments["harness"],
        source_type=arguments["source_type"],
        source_name="fixture.jsonl",
        source_position=arguments.get("source_position", fixture.TEN_TEXT),
        session_id=domain_ids.SessionId(fixture.SESSION_ONE_ID),
        actor_id=domain_ids.ActorId(fixture.SESSION_ONE_LEAD_ID),
        parent_actor_id=None,
        observed_at=arguments.get("observed_at", 100.0),
        encoding=("json" if arguments["source_type"] == fixture.HOOK_SOURCE else "jsonl"),
        payload=json.dumps(document).encode(),
    )


def payloads[PayloadType: event_base.EventPayload](
    translation: raw_event_models.TranslationResult,
    payload_type: type[PayloadType],
) -> list[event_base.CanonicalEvent[PayloadType]]:
    """Select translated events with the requested payload type.

    Returns:
        The matching canonical events in translation order.

    """
    return [
        typing.cast("event_base.CanonicalEvent[PayloadType]", event)
        for event in translation.canonical_events
        if isinstance(event.payload, payload_type)
    ]


def committed[PayloadType: event_base.EventPayload](
    payload: PayloadType,
    *,
    parent_actor_id: domain_ids.ActorId | None = None,
) -> event_base.CanonicalEvent[PayloadType]:
    """Wrap a payload in a canonical event for the fixed Claude session.

    Returns:
        The event with an identity based on its payload type.

    """
    return event_base.CanonicalEvent(
        domain_ids.CanonicalEventId(f"event-{type(payload).__name__}"),
        domain_ids.SessionId(fixture.SESSION_ONE_ID),
        domain_ids.ActorId(fixture.SESSION_ONE_LEAD_ID),
        None,
        parent_actor_id,
        CLAUDE_CODE_HARNESS,
        fixture.FIXTURE_EVENT_TIME,
        None,
        None,
        payload,
    )
