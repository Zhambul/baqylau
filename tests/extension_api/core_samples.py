# Copyright (c) 2026 Zhambyl Yermagambet
"""Load full core payload fixtures and build private event envelopes."""

from pathlib import Path

from baqylau_extension_api.core.payloads import CorePayload
from pydantic import TypeAdapter

from domain import events, ids
from domain.event_base import CanonicalEvent, EventPayload

PAYLOAD_ADAPTER: TypeAdapter[tuple[CorePayload, ...]] = TypeAdapter(tuple[CorePayload, ...])
FIXTURE_PATH = Path(__file__).with_name("fixtures") / "core_payloads.json"
PAYLOADS = PAYLOAD_ADAPTER.validate_json(FIXTURE_PATH.read_bytes())
PROCESS_ID = 1234
COMMIT_CURSOR = 17
SOURCE_TIME = 1.25
ACCEPTANCE_TIME = 2.5


def original_payload(payload: CorePayload) -> EventPayload:
    """Decode fixture data with the independent private domain codec.

    Returns:
        A real domain dataclass with the fixture's non-default fields.

    """
    adapter = TypeAdapter(events.PAYLOAD_TYPES[payload.kind])
    return adapter.validate_json(payload.model_dump_json(exclude={"kind"}))


def original_event(payload: EventPayload) -> CanonicalEvent[EventPayload]:
    """Build a private event with all envelope fields set.

    Returns:
        A committed event with opaque source and actor identifiers.

    """
    return CanonicalEvent(
        event_id=ids.CanonicalEventId("event/native/世界"),
        session_id=ids.SessionId("session/native/1"),
        actor_id=ids.ActorId("actor/native/1"),
        harness=ids.HarnessName("test"),
        turn_id=ids.TurnId("turn/native/1"),
        parent_actor_id=ids.ActorId("actor/native/parent"),
        occurred_at=SOURCE_TIME,
        terminal_window_id=ids.WindowId("window/native/1"),
        harness_process_id=PROCESS_ID,
        payload=payload,
        cursor=COMMIT_CURSOR,
        accepted_at=ACCEPTANCE_TIME,
        raw_event_ids=(ids.RawEventId("raw/path/1"), ids.RawEventId("raw/path/2")),
    )
