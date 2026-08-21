"""Row DTO to model object, for the raw event and fact tables.

Pure functions: no I/O, no SQL, no clock, no driver. Everything that used to be
inline tuple-building and hand-rolled row reading inside the store classes lives
here, once — the row-to-`RawEvent` mapping in particular existed twice, in the
canonical store and again in the raw event queries.

The canonical event's stored form lives here too: the identity columns, the
schema version that decides how to read them, and the payload — the twelve
names a canonical fact is split across when it is stored, and put back
together when it is read. Declared once. The same twelve names used to be
written out four times: a dict literal on the way out, a set of strings to
check on the way in, a column split in this file, and a re-assembly to hand a
stored row back to a decoder — with nothing holding any of the four to the
others.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from typing import Any, Generic

from pydantic import ConfigDict, TypeAdapter, ValidationError

from domain.events import (
    SCHEMA_VERSION,
    CanonicalEvent,
    EVENT_TYPES,
    PAYLOAD_TYPES,
    EventPayload,
    EventPayloadType,
)
from domain.ids import (
    ActorId,
    CanonicalEventId,
    ShellId,
    RawEventId,
    SessionId,
    TurnId,
    WindowId,
)
from domain.shells import ShellOutputFollowing
from domain.records import (
    InterpretationEventRecord,
    StoredCanonicalEvent,
    InterpretationRecord,
)
from domain.stored import STORED
from harness.models import RawEvent, Session
from repository.mapper.documents import StoredDocumentError
from repository.model.facts import (
    CanonicalEventRow,
    ShellOutputRow,
    RawEventRow,
    SessionRow,
)
from repository.model.sql import SqlValues

# --- sessions -----------------------------------------------------------------


def session(session_row: SessionRow) -> Session:
    return Session(
        session_id=SessionId(session_row.session_id),
        lead_actor_id=ActorId(session_row.lead_actor_id),
        harness_session_id=session_row.harness_session_id,
        source_reference=session_row.source_reference,
        working_directory=session_row.working_directory,
        terminal_window_id=session_row.terminal_window_id,
        harness_process_id=session_row.harness_process_id,
    )


def session_values(harness: str, session: Session, created_at: float) -> SqlValues:
    return (
        str(session.session_id),
        str(session.lead_actor_id),
        harness,
        session.harness_session_id,
        session.source_reference,
        session.working_directory,
        session.terminal_window_id,
        session.harness_process_id,
        created_at,
    )


# --- raw events ----------------------------------------------------------------


def raw_event(raw_event_row: RawEventRow) -> RawEvent:
    return RawEvent(
        raw_event_id=RawEventId(raw_event_row.raw_event_id),
        harness=raw_event_row.harness,
        source_type=raw_event_row.source_type,
        source_name=raw_event_row.source_name,
        source_position=raw_event_row.source_position,
        session_id=SessionId(raw_event_row.session_id),
        actor_id=ActorId(raw_event_row.actor_id),
        parent_actor_id=(
            ActorId(raw_event_row.parent_actor_id)
            if raw_event_row.parent_actor_id is not None
            else None
        ),
        observed_at=raw_event_row.observed_at,
        encoding=raw_event_row.encoding,
        payload=raw_event_row.payload,
        source_identity=raw_event_row.source_identity,
        terminal_window_id=raw_event_row.terminal_window_id,
        harness_process_id=raw_event_row.harness_process_id,
        account_id=raw_event_row.account_id,
        account_display_name=raw_event_row.account_display_name,
    )


def raw_event_values(raw_event: RawEvent) -> SqlValues:
    return (
        str(raw_event.raw_event_id),
        str(raw_event.session_id),
        raw_event.harness,
        raw_event.source_type,
        raw_event.source_identity or raw_event.source_type,
        raw_event.source_name,
        raw_event.source_position,
        str(raw_event.actor_id),
        str(raw_event.parent_actor_id) if raw_event.parent_actor_id is not None else None,
        raw_event.observed_at,
        raw_event.encoding,
        raw_event.payload,
        raw_event.terminal_window_id,
        raw_event.harness_process_id,
        raw_event.account_id,
        raw_event.account_display_name,
    )


def raw_identity(raw_event: RawEvent) -> SqlValues:
    """The columns that decide whether a re-record is the SAME observation.

    Re-recording an identical observation is a no-op by design; reusing an id
    for different bytes is corruption. This tuple is what tells them apart, and
    its column order is the `SELECT` that reads it back.
    """
    return (
        str(raw_event.session_id),
        raw_event.harness,
        raw_event.source_type,
        raw_event.source_name,
        raw_event.source_position,
        str(raw_event.actor_id),
        str(raw_event.parent_actor_id) if raw_event.parent_actor_id is not None else None,
        raw_event.encoding,
        raw_event.payload,
    )


# --- canonical facts ----------------------------------------------------------


@dataclass(frozen=True)
class CanonicalEventDocument(Generic[EventPayloadType]):
    """One canonical event as it is STORED: the identity columns, the schema
    version that decides how to read them, and the payload.

    Declared once, so the twelve names below have one place to drift from
    instead of four.
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
    terminal_window_id: WindowId | None
    turn_id: TurnId | None

    __pydantic_config__ = STORED


@dataclass(frozen=True)
class _StoredEventType:
    """Which payload a stored document holds — the one field that has to be
    read before the rest can be, since it is what says how to read the rest.

    A declaration with `extra="ignore"`, so reading it is a validation like
    every other and this module needs no `json` of its own.
    """

    __pydantic_config__ = ConfigDict(extra="ignore")

    event_type: str


_EVENT_TYPE = TypeAdapter(_StoredEventType)


def _stored_event_type(encoded: bytes | str) -> str:
    try:
        event_type = _EVENT_TYPE.validate_json(encoded).event_type
    except ValidationError as error:
        raise StoredDocumentError("stored canonical event names no event type") from error
    if event_type not in PAYLOAD_TYPES:
        raise StoredDocumentError(f"unknown canonical event type: {event_type!r}")
    return event_type


def _event_type(event_payload: EventPayload) -> str:
    """The registered name of a payload's type — the discriminator the stored
    document carries, and the key everything below is cached on."""
    try:
        return EVENT_TYPES[type(event_payload)]
    except KeyError as error:
        raise StoredDocumentError(
            f"unregistered canonical payload: {type(event_payload).__name__}"
        ) from error


@cache
def _document_adapter(event_type: str) -> TypeAdapter[Any]:
    """The validator/serializer for one event type's stored document. Cached
    because building a schema is not free and there are exactly as many of
    these as there are registered event types.

    Keyed on the event type rather than the class, because the event type is
    what the stored document actually says.
    """
    document: Any = CanonicalEventDocument
    return TypeAdapter(document[PAYLOAD_TYPES[event_type]])


@cache
def _payload_adapter(event_type: str) -> TypeAdapter[Any]:
    """The same, for the column that holds only the payload."""
    return TypeAdapter(PAYLOAD_TYPES[event_type])


def canonical_event_document(canonical_event: CanonicalEvent[EventPayload]) -> CanonicalEventDocument[EventPayload]:
    """The event as it will be stored, VALIDATED."""
    event_type = _event_type(canonical_event.payload)
    document = CanonicalEventDocument(
        actor_id=canonical_event.actor_id,
        event_id=canonical_event.event_id,
        event_type=event_type,
        harness=canonical_event.harness,
        harness_process_id=canonical_event.harness_process_id,
        occurred_at=canonical_event.occurred_at,
        parent_actor_id=canonical_event.parent_actor_id,
        payload=canonical_event.payload,
        schema_version=SCHEMA_VERSION,
        session_id=canonical_event.session_id,
        terminal_window_id=canonical_event.terminal_window_id,
        turn_id=canonical_event.turn_id,
    )
    try:
        _document_adapter(event_type).validate_python(document)
    except ValidationError as error:
        raise StoredDocumentError(f"invalid canonical event: {error}") from error
    return document


def canonical_event(canonical_event_document: CanonicalEventDocument[EventPayload]) -> CanonicalEvent[EventPayload]:
    """A stored document back into the event it holds."""
    if canonical_event_document.schema_version != SCHEMA_VERSION:
        raise StoredDocumentError(
            f"unsupported canonical schema version: {canonical_event_document.schema_version!r}"
        )
    return CanonicalEvent(
        event_id=canonical_event_document.event_id,
        session_id=canonical_event_document.session_id,
        actor_id=canonical_event_document.actor_id,
        turn_id=canonical_event_document.turn_id,
        parent_actor_id=canonical_event_document.parent_actor_id,
        harness=canonical_event_document.harness,
        occurred_at=canonical_event_document.occurred_at,
        terminal_window_id=canonical_event_document.terminal_window_id,
        harness_process_id=canonical_event_document.harness_process_id,
        payload=canonical_event_document.payload,
    )


def encode_canonical_event(canonical_event: CanonicalEvent[EventPayload]) -> bytes:
    return _document_adapter(_event_type(canonical_event.payload)).dump_json(
        canonical_event_document(canonical_event)
    )


def decode_canonical_event(encoded: bytes | str) -> CanonicalEvent[EventPayload]:
    # The payload's type is what a SIBLING field says it is, so the event
    # type is read first and the document validates already parameterized by
    # it — the whole document in one pass, against one declaration.
    event_type = _stored_event_type(encoded)
    try:
        document: CanonicalEventDocument[EventPayload] = _document_adapter(event_type).validate_json(encoded)
    except ValidationError as error:
        raise StoredDocumentError(f"invalid stored canonical event: {error}") from error
    return canonical_event(document)


def payload_json(canonical_event: CanonicalEvent[EventPayload]) -> str:
    """Just the payload, for the column that holds it beside the identity
    ones."""
    adapter = _payload_adapter(_event_type(canonical_event.payload))
    return adapter.dump_json(canonical_event.payload).decode("utf-8")


def payload(event_type: str, encoded_payload: str) -> EventPayload:
    """A stored payload column back into the object it holds."""
    if event_type not in PAYLOAD_TYPES:
        raise StoredDocumentError(f"unknown canonical event type: {event_type!r}")
    try:
        decoded: EventPayload = _payload_adapter(event_type).validate_json(encoded_payload)
    except ValidationError as error:
        raise StoredDocumentError(f"invalid canonical payload: {error}") from error
    return decoded


def canonical_event_values(canonical_event: CanonicalEvent[EventPayload], accepted_at: float) -> SqlValues:
    """The stored document, split across the columns that hold it.

    Building one is what refuses a payload that does not match its declared
    shape, before it reaches storage.

    This used to encode the whole event to JSON and parse it straight back to
    reach its own fields, keying into the resulting dict twelve times.
    """
    document = canonical_event_document(canonical_event)
    return (
        str(document.event_id),
        document.schema_version,
        document.event_type,
        str(document.session_id),
        str(document.actor_id),
        str(document.turn_id) if document.turn_id is not None else None,
        str(document.parent_actor_id) if document.parent_actor_id is not None else None,
        document.harness,
        document.occurred_at,
        document.terminal_window_id,
        document.harness_process_id,
        accepted_at,
        payload_json(canonical_event),
    )


def stored_canonical_event(
    canonical_event_row: CanonicalEventRow,
    raw_event_ids: tuple[RawEventId, ...],
) -> StoredCanonicalEvent:
    return StoredCanonicalEvent(
        cursor=canonical_event_row.cursor,
        accepted_at=canonical_event_row.accepted_at,
        event=canonical_event(row_canonical_event_document(canonical_event_row)),
        raw_event_ids=raw_event_ids,
    )


def row_canonical_event_document(canonical_event_row: CanonicalEventRow) -> CanonicalEventDocument[EventPayload]:
    """A stored row back into the document its columns are.

    Straight across, no bytes in between: the read used to re-serialize the row
    into a JSON document purely so that a decoder could parse it again.
    """
    return CanonicalEventDocument(
        actor_id=ActorId(canonical_event_row.actor_id),
        event_id=CanonicalEventId(canonical_event_row.event_id),
        event_type=canonical_event_row.event_type,
        harness=canonical_event_row.harness,
        harness_process_id=canonical_event_row.harness_process_id,
        occurred_at=canonical_event_row.occurred_at,
        parent_actor_id=(
            ActorId(canonical_event_row.parent_actor_id)
            if canonical_event_row.parent_actor_id is not None
            else None
        ),
        payload=payload(canonical_event_row.event_type, canonical_event_row.payload),
        schema_version=canonical_event_row.schema_version,
        session_id=SessionId(canonical_event_row.session_id),
        terminal_window_id=canonical_event_row.terminal_window_id,
        turn_id=TurnId(canonical_event_row.turn_id) if canonical_event_row.turn_id is not None else None,
    )


# --- interpretations ----------------------------------------------------------


def interpretation_record_values(interpretation_record: InterpretationRecord) -> SqlValues:
    return (
        str(interpretation_record.raw_event_id),
        interpretation_record.translator_version,
        interpretation_record.decision,
        interpretation_record.reason,
        interpretation_record.completed_at,
    )


def interpretation_event_values(
    interpretation_event_record: InterpretationEventRecord,
) -> SqlValues:
    return (
        str(interpretation_event_record.event_id),
        str(interpretation_event_record.raw_event_id),
        interpretation_event_record.event_order,
        interpretation_event_record.storage_result,
    )


# --- shell output -------------------------------------------------------------


def shell_output_following(shell_output_row: ShellOutputRow) -> ShellOutputFollowing:
    until: str = shell_output_row.until
    state: str = shell_output_row.state
    return ShellOutputFollowing(
        session_id=SessionId(shell_output_row.session_id),
        shell_id=ShellId(shell_output_row.shell_id),
        harness=shell_output_row.harness,
        actor_id=ActorId(shell_output_row.actor_id),
        parent_actor_id=(
            ActorId(shell_output_row.parent_actor_id)
            if shell_output_row.parent_actor_id is not None
            else None
        ),
        source_path=shell_output_row.source_path,
        chunk_source_type=shell_output_row.chunk_source_type,
        delete_source=bool(shell_output_row.delete_source),
        initial_size=int(shell_output_row.initial_size),
        initial_modified_at=int(shell_output_row.initial_modified_at),
        wait_for_source_change=bool(shell_output_row.wait_for_source_change),
        until=until,  # type: ignore[arg-type]
        state=state,  # type: ignore[arg-type]
        created_at=shell_output_row.created_at,
    )


def shell_output_values(shell_output_following: ShellOutputFollowing) -> SqlValues:
    return (
        str(shell_output_following.session_id),
        str(shell_output_following.shell_id),
        shell_output_following.harness,
        str(shell_output_following.actor_id),
        (
            str(shell_output_following.parent_actor_id)
            if shell_output_following.parent_actor_id is not None
            else None
        ),
        shell_output_following.source_path,
        shell_output_following.chunk_source_type,
        1 if shell_output_following.delete_source else 0,
        shell_output_following.initial_size,
        shell_output_following.initial_modified_at,
        1 if shell_output_following.wait_for_source_change else 0,
        shell_output_following.until,
        shell_output_following.state,
        shell_output_following.created_at,
    )
