"""Row DTO to model object, for the evidence and fact tables.

Pure functions: no I/O, no SQL, no clock, no driver. Everything that used to be
inline tuple-building and hand-rolled row reading inside the store classes lives
here, once — the row-to-`RawEvent` mapping in particular existed twice, in the
canonical store and again in the evidence queries.
"""

from __future__ import annotations

import json

from domain.codec import CanonicalEventCodec
from domain.events import CanonicalEvent, EventPayload
from domain.ids import (
    ActorId,
    OperationId,
    RawEventId,
    SessionId,
)
from domain.operations import OperationOutputFollowing
from domain.records import (
    InterpretationEventRecord,
    StoredCanonicalEvent,
    InterpretationRecord,
)
from harness.models import RawEvent, Session
from repository.model.facts import (
    CanonicalEventRow,
    OperationOutputRow,
    RawEventRow,
    SessionRow,
)

# --- sessions -----------------------------------------------------------------


def session(row: SessionRow) -> Session:
    return Session(
        session_id=SessionId(row.session_id),
        lead_actor_id=ActorId(row.lead_actor_id),
        harness_session_id=row.harness_session_id,
        source_reference=row.source_reference,
        working_directory=row.working_directory,
        terminal_window_id=row.terminal_window_id,
        harness_process_id=row.harness_process_id,
    )


def session_values(harness: str, value: Session, created_at: float) -> tuple[object, ...]:
    return (
        str(value.session_id),
        str(value.lead_actor_id),
        harness,
        value.harness_session_id,
        value.source_reference,
        value.working_directory,
        value.terminal_window_id,
        value.harness_process_id,
        created_at,
    )


# --- raw evidence -------------------------------------------------------------


def raw_event(row: RawEventRow) -> RawEvent:
    return RawEvent(
        raw_event_id=RawEventId(row.raw_event_id),
        harness=row.harness,
        source_type=row.source_type,
        source_name=row.source_name,
        source_position=row.source_position,
        session_id=SessionId(row.session_id),
        actor_id=ActorId(row.actor_id),
        parent_actor_id=(ActorId(row.parent_actor_id) if row.parent_actor_id is not None else None),
        observed_at=row.observed_at,
        encoding=row.encoding,
        payload=row.payload,
        source_identity=row.source_identity,
        terminal_window_id=row.terminal_window_id,
        harness_process_id=row.harness_process_id,
        account_id=row.account_id,
        account_display_name=row.account_display_name,
    )


def raw_event_values(value: RawEvent) -> tuple[object, ...]:
    return (
        str(value.raw_event_id),
        str(value.session_id),
        value.harness,
        value.source_type,
        value.source_identity or value.source_type,
        value.source_name,
        value.source_position,
        str(value.actor_id),
        str(value.parent_actor_id) if value.parent_actor_id is not None else None,
        value.observed_at,
        value.encoding,
        value.payload,
        value.terminal_window_id,
        value.harness_process_id,
        value.account_id,
        value.account_display_name,
    )


def raw_identity(value: RawEvent) -> tuple[object, ...]:
    """The columns that decide whether a re-record is the SAME observation.

    Re-recording an identical observation is a no-op by design; reusing an id
    for different bytes is corruption. This tuple is what tells them apart, and
    its column order is the `SELECT` that reads it back.
    """
    return (
        str(value.session_id),
        value.harness,
        value.source_type,
        value.source_name,
        value.source_position,
        str(value.actor_id),
        str(value.parent_actor_id) if value.parent_actor_id is not None else None,
        value.encoding,
        value.payload,
    )


# --- canonical facts ----------------------------------------------------------


def canonical_event_values(
    event: CanonicalEvent[EventPayload],
    accepted_at: float,
    codec: CanonicalEventCodec,
) -> tuple[object, ...]:
    """Encode once, then split the document across the columns.

    The codec both serialises and VALIDATES, so encoding here is what refuses a
    payload that does not match its declared shape.
    """
    document = json.loads(codec.encode(event))
    return (
        document["event_id"],
        document["schema_version"],
        document["event_type"],
        document["session_id"],
        document["actor_id"],
        document["turn_id"],
        document["parent_actor_id"],
        document["harness"],
        document["occurred_at"],
        document["terminal_window_id"],
        document["harness_process_id"],
        accepted_at,
        json.dumps(document["payload"], ensure_ascii=False, separators=(",", ":"), sort_keys=True),
    )


def stored_canonical_event(
    row: CanonicalEventRow,
    raw_event_ids: tuple[RawEventId, ...],
    codec: CanonicalEventCodec,
) -> StoredCanonicalEvent:
    return StoredCanonicalEvent(
        cursor=row.cursor,
        accepted_at=row.accepted_at,
        event=codec.decode(_encoded_envelope(row)),
        raw_event_ids=raw_event_ids,
    )


def _encoded_envelope(row: CanonicalEventRow) -> bytes:
    document = {
        "actor_id": row.actor_id,
        "event_id": row.event_id,
        "event_type": row.event_type,
        "harness": row.harness,
        "harness_process_id": row.harness_process_id,
        "occurred_at": row.occurred_at,
        "parent_actor_id": row.parent_actor_id,
        "payload": json.loads(row.payload),
        "schema_version": row.schema_version,
        "session_id": row.session_id,
        "terminal_window_id": row.terminal_window_id,
        "turn_id": row.turn_id,
    }
    return json.dumps(
        document, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")


# --- interpretations ----------------------------------------------------------


def interpretation_record_values(record: InterpretationRecord) -> tuple[object, ...]:
    return (
        str(record.raw_event_id),
        record.translator_version,
        record.decision,
        record.reason,
        record.completed_at,
    )


def interpretation_event_values(entry: InterpretationEventRecord) -> tuple[object, ...]:
    return (
        str(entry.event_id),
        str(entry.raw_event_id),
        entry.event_order,
        entry.storage_result,
    )


# --- operation output ---------------------------------------------------------


def operation_output_following(row: OperationOutputRow) -> OperationOutputFollowing:
    until: str = row.until
    state: str = row.state
    return OperationOutputFollowing(
        session_id=SessionId(row.session_id),
        operation_id=OperationId(row.operation_id),
        harness=row.harness,
        actor_id=ActorId(row.actor_id),
        parent_actor_id=(ActorId(row.parent_actor_id) if row.parent_actor_id is not None else None),
        source_path=row.source_path,
        chunk_source_type=row.chunk_source_type,
        delete_source=bool(row.delete_source),
        initial_size=int(row.initial_size),
        initial_modified_at=int(row.initial_modified_at),
        wait_for_source_change=bool(row.wait_for_source_change),
        until=until,  # type: ignore[arg-type]
        state=state,  # type: ignore[arg-type]
        created_at=row.created_at,
    )


def operation_output_values(following: OperationOutputFollowing) -> tuple[object, ...]:
    return (
        str(following.session_id),
        str(following.operation_id),
        following.harness,
        str(following.actor_id),
        str(following.parent_actor_id) if following.parent_actor_id is not None else None,
        following.source_path,
        following.chunk_source_type,
        1 if following.delete_source else 0,
        following.initial_size,
        following.initial_modified_at,
        1 if following.wait_for_source_change else 0,
        following.until,
        following.state,
        following.created_at,
    )
