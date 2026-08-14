"""Deterministic serialization for the closed canonical event schema."""

from __future__ import annotations

import json
import types
import typing
from dataclasses import fields, is_dataclass
from decimal import Decimal
from functools import cache
from typing import Any, Literal, get_args, get_origin, get_type_hints

from domain.events import CanonicalEvent, EVENT_TYPES, PAYLOAD_TYPES, EventPayload
from domain.ids import ActorId, CanonicalEventId, SessionId, TurnId

SCHEMA_VERSION = 13
FORBIDDEN_PRESENTATION_FIELDS = frozenset({
    "ansi",
    "bubbled",
    "chrome",
    "css",
    "glyph",
    "gutter",
    "html",
    "note",
    "rgb",
    "web",
    "wrap",
})
FORBIDDEN_NATIVE_SOURCE_FIELDS = frozenset({
    "source_reference",
})


class CanonicalCodecError(ValueError):
    pass


@cache
def _annotations(value_type: type) -> dict[str, Any]:
    return get_type_hints(value_type)


def _to_data(value: Any) -> Any:
    if is_dataclass(value):
        return {field.name: _to_data(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, tuple):
        return [_to_data(item) for item in value]
    if isinstance(value, frozenset):
        return sorted((_to_data(item) for item in value), key=repr)
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    raise CanonicalCodecError(f"unsupported canonical value: {type(value).__name__}")


def _from_data(expected_type: Any, value: Any) -> Any:
    if expected_type is Any:
        return value
    if hasattr(expected_type, "__supertype__"):
        return expected_type(_from_data(expected_type.__supertype__, value))

    origin = get_origin(expected_type)
    arguments = get_args(expected_type)
    if origin is Literal:
        if value not in arguments:
            raise CanonicalCodecError(f"invalid literal {value!r}; expected one of {arguments!r}")
        return value
    if origin in (typing.Union, types.UnionType):
        errors = []
        for member_type in arguments:
            try:
                return _from_data(member_type, value)
            except (CanonicalCodecError, TypeError, ValueError) as error:
                errors.append(str(error))
        raise CanonicalCodecError(f"value does not match canonical union: {'; '.join(errors)}")
    if origin is tuple:
        if not isinstance(value, list):
            raise CanonicalCodecError("canonical tuple must be encoded as an array")
        item_type = arguments[0]
        return tuple(_from_data(item_type, item) for item in value)
    if expected_type is Decimal:
        if not isinstance(value, str):
            raise CanonicalCodecError("decimal must be encoded as a string")
        return Decimal(value)
    if is_dataclass(expected_type):
        if not isinstance(value, dict):
            raise CanonicalCodecError(f"{expected_type.__name__} must be encoded as an object")
        expected_fields = {field.name for field in fields(expected_type)}
        actual_fields = set(value)
        if actual_fields != expected_fields:
            missing = sorted(expected_fields - actual_fields)
            extra = sorted(actual_fields - expected_fields)
            raise CanonicalCodecError(
                f"invalid {expected_type.__name__} fields; missing={missing!r}, extra={extra!r}"
            )
        annotations = _annotations(expected_type)
        return expected_type(**{
            name: _from_data(annotations[name], field_value)
            for name, field_value in value.items()
        })
    if expected_type is type(None):
        if value is not None:
            raise CanonicalCodecError("expected null")
        return None
    if expected_type is float and type(value) in (int, float):
        return float(value)
    if expected_type in (str, int, bool):
        if type(value) is not expected_type:
            raise CanonicalCodecError(f"expected {expected_type.__name__}, got {type(value).__name__}")
        return value
    raise CanonicalCodecError(f"unsupported canonical type: {expected_type!r}")


class CanonicalEventCodec:
    def __init__(self) -> None:
        for payload_type in EVENT_TYPES:
            field_names = {field.name for field in fields(payload_type)}
            forbidden_presentation = FORBIDDEN_PRESENTATION_FIELDS.intersection(field_names)
            if forbidden_presentation:
                raise CanonicalCodecError(
                    f"presentation fields are forbidden in {payload_type.__name__}: "
                    f"{sorted(forbidden_presentation)!r}"
                )
            forbidden_sources = FORBIDDEN_NATIVE_SOURCE_FIELDS.intersection(field_names)
            if forbidden_sources:
                raise CanonicalCodecError(
                    f"native source fields are forbidden in {payload_type.__name__}: "
                    f"{sorted(forbidden_sources)!r}"
                )

    def encode(self, event: CanonicalEvent[EventPayload]) -> bytes:
        payload_type = type(event.payload)
        try:
            event_type = EVENT_TYPES[payload_type]
        except KeyError as error:
            raise CanonicalCodecError(f"unregistered canonical payload: {payload_type.__name__}") from error
        payload = _to_data(event.payload)
        _from_data(payload_type, payload)
        document = {
            "actor_id": str(event.actor_id),
            "event_id": str(event.event_id),
            "event_type": event_type,
            "harness": event.harness,
            "occurred_at": event.occurred_at,
            "parent_actor_id": str(event.parent_actor_id) if event.parent_actor_id is not None else None,
            "payload": payload,
            "schema_version": SCHEMA_VERSION,
            "session_id": str(event.session_id),
            "turn_id": str(event.turn_id) if event.turn_id is not None else None,
        }
        return json.dumps(document, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")

    def decode(self, encoded: bytes | str) -> CanonicalEvent[EventPayload]:
        try:
            document = json.loads(encoded)
        except (TypeError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise CanonicalCodecError("canonical event is not valid UTF-8 JSON") from error
        expected_fields = {
            "actor_id",
            "event_id",
            "event_type",
            "harness",
            "occurred_at",
            "parent_actor_id",
            "payload",
            "schema_version",
            "session_id",
            "turn_id",
        }
        if not isinstance(document, dict) or set(document) != expected_fields:
            raise CanonicalCodecError("canonical envelope fields do not match the schema")
        if document["schema_version"] != SCHEMA_VERSION:
            raise CanonicalCodecError(f"unsupported canonical schema version: {document['schema_version']!r}")
        try:
            payload_type = PAYLOAD_TYPES[document["event_type"]]
        except KeyError as error:
            raise CanonicalCodecError(f"unknown canonical event type: {document['event_type']!r}") from error
        payload = _from_data(payload_type, document["payload"])
        return CanonicalEvent(
            event_id=CanonicalEventId(_from_data(str, document["event_id"])),
            session_id=SessionId(_from_data(str, document["session_id"])),
            actor_id=ActorId(_from_data(str, document["actor_id"])),
            turn_id=(TurnId(document["turn_id"]) if document["turn_id"] is not None else None),
            parent_actor_id=(ActorId(document["parent_actor_id"]) if document["parent_actor_id"] is not None else None),
            harness=_from_data(str, document["harness"]),
            occurred_at=(
                _from_data(float, document["occurred_at"])
                if document["occurred_at"] is not None
                else None
            ),
            payload=payload,
        )

    def payload_json(self, event: CanonicalEvent[EventPayload]) -> str:
        return json.dumps(_to_data(event.payload), ensure_ascii=False, separators=(",", ":"), sort_keys=True)
