"""The canonical event's stored form.

One envelope dataclass, and pydantic. There is a codec at all because the store
keeps an event as a JSON blob beside its identity columns, so something has to
turn a `CanonicalEvent` into those bytes and back — and has to REFUSE a payload
that does not match its declared shape, at the write, which is the last moment
the bad value is still attributable to whoever produced it.

What used to be here instead was a hand-written reflective serializer: 120 lines
walking `get_type_hints`, resolving unions, checking Literals, deciding that a
Decimal is a string and a tuple is an array. Every line of it was a
reimplementation of what the api layer already gets from pydantic for free, and
it existed only because `domain/` was allowed no dependency that could do it.
The rule now admits pydantic — which the daemon already runs — and the walk is
gone.

Strictness is `domain/stored.py`'s `STORED` config, named on every shape that is
stored — the payload marker base and the eight value objects a payload can nest.
An unknown field is schema drift and does not decode. A field WITH a default
stays optional, so rows written before that field existed keep decoding —
additive evolution without a rewrite. An int where a float is declared is
accepted, as it always was; anything else of the wrong type is not.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from typing import Any, Generic, TypeVar

from pydantic import ConfigDict, TypeAdapter, ValidationError

from domain.events import (
    CanonicalEvent,
    EVENT_TYPES,
    PAYLOAD_TYPES,
    EventPayload,
    EventPayloadType,
)
from domain.ids import ActorId, CanonicalEventId, SessionId, TurnId
from domain.stored import STORED

SCHEMA_VERSION = 16


class CanonicalCodecError(ValueError):
    pass


@dataclass(frozen=True)
class CanonicalEnvelope(Generic[EventPayloadType]):
    """One canonical event as it is STORED: the identity columns, the schema
    version that decides how to read them, and the payload.

    Declared once. The same twelve names used to be written out four times — a
    dict literal on the way out, a set of strings to check on the way in, a
    column split in the repository mapper, and a re-assembly to hand a stored row
    back to `decode` — with nothing holding any of the four to the others.
    """

    actor_id: ActorId
    event_id: CanonicalEventId
    event_type: str
    harness: str
    harness_process_id: int | None
    occurred_at: float | None
    parent_actor_id: ActorId | None
    payload: EventPayloadType
    schema_version: int
    session_id: SessionId
    terminal_window_id: str | None
    turn_id: TurnId | None

    __pydantic_config__ = STORED


@dataclass(frozen=True)
class _StoredEventType:
    """Which payload a stored document holds — the one field that has to be read
    before the rest can be, since it is what says how to read the rest.

    A declaration with `extra="ignore"`, so reading it is a validation like every
    other and this module needs no `json` of its own.
    """

    __pydantic_config__ = ConfigDict(extra="ignore")

    event_type: str


_EVENT_TYPE = TypeAdapter(_StoredEventType)


def _stored_event_type(encoded: bytes | str) -> str:
    try:
        event_type = _EVENT_TYPE.validate_json(encoded).event_type
    except ValidationError as error:
        raise CanonicalCodecError("canonical envelope names no event type") from error
    if event_type not in PAYLOAD_TYPES:
        raise CanonicalCodecError(f"unknown canonical event type: {event_type!r}")
    return event_type


def _event_type(payload: EventPayload) -> str:
    """The registered name of a payload's type — the discriminator the stored
    document carries, and the key everything below is cached on."""
    try:
        return EVENT_TYPES[type(payload)]
    except KeyError as error:
        raise CanonicalCodecError(
            f"unregistered canonical payload: {type(payload).__name__}"
        ) from error


@cache
def _envelope_adapter(event_type: str) -> TypeAdapter[Any]:
    """The validator/serializer for one event type's envelope. Cached because
    building a schema is not free and there are exactly as many of these as
    there are registered event types.

    Keyed on the event type rather than the class, because the event type is
    what the stored document actually says.
    """
    envelope: Any = CanonicalEnvelope
    return TypeAdapter(envelope[PAYLOAD_TYPES[event_type]])


@cache
def _payload_adapter(event_type: str) -> TypeAdapter[Any]:
    """The same, for the column that holds only the payload."""
    return TypeAdapter(PAYLOAD_TYPES[event_type])


DocumentType = TypeVar("DocumentType")


def encode_document(value: object) -> bytes:
    """Any dataclass of ours as the bytes it is stored or carried as.

    Not only the canonical envelope: the engine's own synthetic evidence — an
    output chunk, a process exit, an interrupt mark — was a dict literal at the
    writer and a field-by-field read at the translator, twice per document, with
    nothing holding the two halves together.
    """
    adapter: TypeAdapter[Any] = TypeAdapter(type(value))
    return adapter.dump_json(value)


def decode_document(shape: type[DocumentType], encoded: bytes | str) -> DocumentType:
    """The inverse, against the shape the caller expects."""
    adapter: TypeAdapter[DocumentType] = TypeAdapter(shape)
    try:
        return adapter.validate_json(encoded)
    except ValidationError as error:
        raise CanonicalCodecError(f"not a {shape.__name__}: {error}") from error


class CanonicalEventCodec:
    """The stored form, in both directions.

    Four operations, because the store needs two of them WITHOUT bytes: a write
    splits the envelope across columns, and a read rebuilds one from them. Those
    used to go through `encode` and a re-parse — every read serialized the event
    to JSON and immediately parsed it again to reach its own columns.
    """

    def envelope(self, event: CanonicalEvent[EventPayload]) -> CanonicalEnvelope[EventPayload]:
        """The event as it will be stored, VALIDATED."""
        event_type = _event_type(event.payload)
        envelope = CanonicalEnvelope(
            actor_id=event.actor_id,
            event_id=event.event_id,
            event_type=event_type,
            harness=event.harness,
            harness_process_id=event.harness_process_id,
            occurred_at=event.occurred_at,
            parent_actor_id=event.parent_actor_id,
            payload=event.payload,
            schema_version=SCHEMA_VERSION,
            session_id=event.session_id,
            terminal_window_id=event.terminal_window_id,
            turn_id=event.turn_id,
        )
        try:
            _envelope_adapter(event_type).validate_python(envelope)
        except ValidationError as error:
            raise CanonicalCodecError(f"invalid canonical event: {error}") from error
        return envelope

    def event(self, envelope: CanonicalEnvelope[EventPayload]) -> CanonicalEvent[EventPayload]:
        """A stored envelope back into the event it holds."""
        if envelope.schema_version != SCHEMA_VERSION:
            raise CanonicalCodecError(
                f"unsupported canonical schema version: {envelope.schema_version!r}"
            )
        return CanonicalEvent(
            event_id=envelope.event_id,
            session_id=envelope.session_id,
            actor_id=envelope.actor_id,
            turn_id=envelope.turn_id,
            parent_actor_id=envelope.parent_actor_id,
            harness=envelope.harness,
            occurred_at=envelope.occurred_at,
            terminal_window_id=envelope.terminal_window_id,
            harness_process_id=envelope.harness_process_id,
            payload=envelope.payload,
        )

    def encode(self, event: CanonicalEvent[EventPayload]) -> bytes:
        return _envelope_adapter(_event_type(event.payload)).dump_json(self.envelope(event))

    def decode(self, encoded: bytes | str) -> CanonicalEvent[EventPayload]:
        # The payload's type is what a SIBLING field says it is, so the event
        # type is read first and the envelope validates already parameterized by
        # it — the whole document in one pass, against one declaration.
        event_type = _stored_event_type(encoded)
        try:
            envelope: CanonicalEnvelope[EventPayload] = _envelope_adapter(
                event_type
            ).validate_json(encoded)
        except ValidationError as error:
            raise CanonicalCodecError(f"invalid canonical envelope: {error}") from error
        return self.event(envelope)

    def payload_json(self, event: CanonicalEvent[EventPayload]) -> str:
        """Just the payload, for the column that holds it beside the identity
        ones."""
        adapter = _payload_adapter(_event_type(event.payload))
        return adapter.dump_json(event.payload).decode("utf-8")

    def payload(self, event_type: str, payload_json: str) -> EventPayload:
        """A stored payload column back into the object it holds."""
        if event_type not in PAYLOAD_TYPES:
            raise CanonicalCodecError(f"unknown canonical event type: {event_type!r}")
        try:
            decoded: EventPayload = _payload_adapter(event_type).validate_json(payload_json)
        except ValidationError as error:
            raise CanonicalCodecError(f"invalid canonical payload: {error}") from error
        return decoded
