"""Row DTO to model object, for the evidence and fact tables.

Pure functions: no I/O, no SQL, no clock, no driver. Everything that used to be
inline tuple-building and hand-rolled row reading inside the store classes lives
here, once — the row-to-`RawEvent` mapping in particular existed twice, in the
canonical store and again in the evidence queries.
"""

from __future__ import annotations

from domain.codec import CanonicalEnvelope, CanonicalEventCodec
from domain.events import CanonicalEvent, EventPayload
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
    StoredCanonicalEvent,
    InterpretationRecord,
)
from harness.models import RawEvent, Session
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


# --- raw evidence -------------------------------------------------------------


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


def canonical_event_values(
    event: CanonicalEvent[EventPayload],
    accepted_at: float,
    canonical_event_codec: CanonicalEventCodec,
) -> SqlValues:
    """The envelope, split across the columns that hold it.

    The codec both builds the envelope and VALIDATES it, so asking for one here
    is what refuses a payload that does not match its declared shape.

    This used to encode the whole event to JSON and parse it straight back to
    reach its own fields, keying into the resulting dict twelve times.
    """
    envelope = canonical_event_codec.envelope(event)
    return (
        str(envelope.event_id),
        envelope.schema_version,
        envelope.event_type,
        str(envelope.session_id),
        str(envelope.actor_id),
        str(envelope.turn_id) if envelope.turn_id is not None else None,
        str(envelope.parent_actor_id) if envelope.parent_actor_id is not None else None,
        envelope.harness,
        envelope.occurred_at,
        envelope.terminal_window_id,
        envelope.harness_process_id,
        accepted_at,
        canonical_event_codec.payload_json(event),
    )


def stored_canonical_event(
    canonical_event_row: CanonicalEventRow,
    raw_event_ids: tuple[RawEventId, ...],
    canonical_event_codec: CanonicalEventCodec,
) -> StoredCanonicalEvent:
    return StoredCanonicalEvent(
        cursor=canonical_event_row.cursor,
        accepted_at=canonical_event_row.accepted_at,
        event=canonical_event_codec.event(
            canonical_envelope(canonical_event_row, canonical_event_codec)
        ),
        raw_event_ids=raw_event_ids,
    )


def canonical_envelope(
    canonical_event_row: CanonicalEventRow, canonical_event_codec: CanonicalEventCodec
) -> CanonicalEnvelope[EventPayload]:
    """A stored row back into the envelope its columns are.

    Straight across, no bytes in between: the read used to re-serialize the row
    into a JSON document purely so that `decode` could parse it again.
    """
    return CanonicalEnvelope(
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
        payload=canonical_event_codec.payload(
            canonical_event_row.event_type, canonical_event_row.payload
        ),
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
