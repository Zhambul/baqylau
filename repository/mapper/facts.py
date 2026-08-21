"""Row DTO to model object, for the raw event and fact tables.

Pure functions: no I/O, no SQL, no clock, no driver. Everything that used to be
inline tuple-building and hand-rolled row reading inside the store classes lives
here, once — the row-to-`RawEvent` mapping in particular existed twice, in the
canonical store and again in the raw event queries.

The canonical event's own store split lives here too: which columns its
identity is spread across, and which column holds the payload. One class,
`domain.events.CanonicalEvent`, on both sides — nothing here declares a second
shape to carry it. The twelve identity names used to be written out four
times: a dict literal on the way out, a set of strings to check on the way in,
a column split in this file, and a re-assembly to hand a stored row back to a
decoder — with nothing holding any of the four to the others.
"""

from __future__ import annotations

from functools import cache
from typing import Any

from pydantic import TypeAdapter, ValidationError

from domain.events import (
    SCHEMA_VERSION,
    CanonicalEvent,
    EVENT_TYPES,
    PAYLOAD_TYPES,
    EventPayload,
)
from domain.ids import (
    ActorId,
    CanonicalEventId,
    ShellId,
    RawEventId,
    SessionId,
    TurnId,
)
from domain.shells import ShellOutputFollowing
from domain.records import (
    InterpretationEventRecord,
    InterpretationRecord,
)
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
#
# One class, `domain.events.CanonicalEvent`, on both sides of storage. Nothing
# here declares a second shape to hold it: the identity columns and the payload
# are read and written straight to and from the fields the event already has.


def _event_type(event_payload: EventPayload) -> str:
    """The registered name of a payload's type — the discriminator the store
    keys its two adapters on, and the value the `event_type` column holds."""
    try:
        return EVENT_TYPES[type(event_payload)]
    except KeyError as error:
        raise StoredDocumentError(
            f"unregistered canonical payload: {type(event_payload).__name__}"
        ) from error


@cache
def _event_adapter(event_type: str) -> TypeAdapter[Any]:
    """The validator for one event type's `CanonicalEvent`. Cached because
    building a schema is not free and there are exactly as many of these as
    there are registered event types.

    VALIDATION only: nothing dumps this adapter's JSON, because the event's
    identity columns are never serialized together — they are typed SQL
    columns, and only the payload is ever turned into bytes.
    """
    event: Any = CanonicalEvent
    return TypeAdapter(event[PAYLOAD_TYPES[event_type]])


@cache
def _payload_adapter(event_type: str) -> TypeAdapter[Any]:
    """The validator/serializer for one event type's payload column."""
    return TypeAdapter(PAYLOAD_TYPES[event_type])


def _validated(canonical_event: CanonicalEvent[EventPayload]) -> str:
    """Check the event against its own registered shape, and answer its
    event type. The last moment a bad value is still attributable to whoever
    produced it, so a write always calls this first."""
    event_type = _event_type(canonical_event.payload)
    try:
        _event_adapter(event_type).validate_python(canonical_event)
    except ValidationError as error:
        raise StoredDocumentError(f"invalid canonical event: {error}") from error
    return event_type


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


def encode_canonical_event(canonical_event: CanonicalEvent[EventPayload]) -> bytes:
    """The event, whole, as a JSON document — for a debugging tool to show a
    human. Not the stored form: the store never serializes more than the
    payload column; the identity columns stay typed SQL columns."""
    return _event_adapter(_validated(canonical_event)).dump_json(canonical_event)


def canonical_event_values(canonical_event: CanonicalEvent[EventPayload], accepted_at: float) -> SqlValues:
    """The event, split across the columns that hold it.

    Validating first is what refuses a payload that does not match its
    declared shape, before it reaches storage.
    """
    event_type = _validated(canonical_event)
    return (
        str(canonical_event.event_id),
        SCHEMA_VERSION,
        event_type,
        str(canonical_event.session_id),
        str(canonical_event.actor_id),
        str(canonical_event.turn_id) if canonical_event.turn_id is not None else None,
        str(canonical_event.parent_actor_id) if canonical_event.parent_actor_id is not None else None,
        canonical_event.harness,
        canonical_event.occurred_at,
        canonical_event.terminal_window_id,
        canonical_event.harness_process_id,
        accepted_at,
        payload_json(canonical_event),
    )


def row_canonical_event(
    canonical_event_row: CanonicalEventRow,
    raw_event_ids: tuple[RawEventId, ...] = (),
) -> CanonicalEvent[EventPayload]:
    """A stored row back into the ONE event class, `cursor`/`accepted_at` set
    from their columns. `raw_event_ids` stays empty unless the caller is the
    audit read that needs it — a range read over `canonical_events` would
    otherwise pay for a second query per page across every session.
    """
    if canonical_event_row.schema_version != SCHEMA_VERSION:
        raise StoredDocumentError(
            f"unsupported canonical schema version: {canonical_event_row.schema_version!r}"
        )
    return CanonicalEvent(
        event_id=CanonicalEventId(canonical_event_row.event_id),
        session_id=SessionId(canonical_event_row.session_id),
        actor_id=ActorId(canonical_event_row.actor_id),
        turn_id=TurnId(canonical_event_row.turn_id) if canonical_event_row.turn_id is not None else None,
        parent_actor_id=(
            ActorId(canonical_event_row.parent_actor_id)
            if canonical_event_row.parent_actor_id is not None
            else None
        ),
        harness=canonical_event_row.harness,
        occurred_at=canonical_event_row.occurred_at,
        terminal_window_id=canonical_event_row.terminal_window_id,
        harness_process_id=canonical_event_row.harness_process_id,
        payload=payload(canonical_event_row.event_type, canonical_event_row.payload),
        cursor=canonical_event_row.cursor,
        accepted_at=canonical_event_row.accepted_at,
        raw_event_ids=raw_event_ids,
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
