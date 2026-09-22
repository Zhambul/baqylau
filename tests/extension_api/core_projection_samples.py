# Copyright (c) 2026 Zhambyl Yermagambet
"""Load full core read-model fixtures and decode them with private codecs."""

from pathlib import Path

from baqylau_extension_api.core.aggregate import CoreAggregateState
from baqylau_extension_api.core.entries import CoreEntryBody
from pydantic import TypeAdapter

from domain import entries, ids
from domain.entry_base import EntryBody

FIXTURES = Path(__file__).with_name("fixtures")
BODY_ADAPTER: TypeAdapter[tuple[CoreEntryBody, ...]] = TypeAdapter(tuple[CoreEntryBody, ...])
BODIES = BODY_ADAPTER.validate_json((FIXTURES / "core_entries.json").read_bytes())
AGGREGATE = CoreAggregateState.model_validate_json((FIXTURES / "core_aggregate.json").read_bytes())
COMMIT_CURSOR = 17
SOURCE_TIME = 1.25


def original_body(body: CoreEntryBody) -> EntryBody:
    """Decode a body fixture using the independent private domain codec.

    Returns:
        A stored body with every fixture field retained.

    """
    adapter = TypeAdapter(entries.BODY_TYPES[entries.EntryTypeName(body.kind)])
    return adapter.validate_json(body.model_dump_json(exclude={"kind"}))


def original_entry(body: CoreEntryBody) -> entries.SessionEntry:
    """Set every core feed envelope field, including its stored cursor.

    Returns:
        A private row for full public-to-private round-trip tests.

    """
    return entries.SessionEntry(
        entry_id=ids.CanonicalEventId("entry/native/世界"), session_id=ids.SessionId("session/native/1"),
        actor_id=ids.ActorId("actor/native/1"), parent_actor_id=ids.ActorId("actor/native/parent"),
        turn_id=ids.TurnId("turn/native/1"), occurred_at=SOURCE_TIME, summary="Fixture summary",
        body=original_body(body), cursor=COMMIT_CURSOR,
    )
