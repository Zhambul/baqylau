"""Row DTO to read-model object.

The mirror of `repository/mapper/facts.py` for the three read-model tables: the
identity columns are read as themselves, and the payload column is decoded
against the shape its own `entry_type` names — the same discriminator-then-shape
pass the canonical codec makes.
"""

from __future__ import annotations

from domain.codec import CanonicalCodecError, decode_document
from domain.entries import BODY_TYPES, EntryBody, SessionEntry
from domain.ids import ActorId, CanonicalEventId, SessionId, TurnId
from domain.sessiondata import ActorFacts, SessionFacts
from repository.model.facts import SessionDataActorRow, SessionDataRow, SessionEntryRow


def session_facts(row: SessionDataRow) -> SessionFacts:
    return decode_document(SessionFacts, row.payload)


def actor_facts(row: SessionDataActorRow) -> ActorFacts:
    return decode_document(ActorFacts, row.payload)


def session_entry(row: SessionEntryRow) -> SessionEntry:
    # The column is TEXT, so what it holds is a promise rather than a type: a row
    # naming a kind this build has no body for is drift, and it says so here
    # rather than decoding into whatever shape happens to fit.
    body_type: type[EntryBody] | None = next(
        (body for name, body in BODY_TYPES.items() if name == row.entry_type), None
    )
    if body_type is None:
        raise CanonicalCodecError(f"unknown entry type: {row.entry_type!r}")
    return SessionEntry(
        entry_id=CanonicalEventId(row.entry_id),
        session_id=SessionId(row.session_id),
        actor_id=ActorId(row.actor_id),
        parent_actor_id=ActorId(row.parent_actor_id) if row.parent_actor_id else None,
        turn_id=TurnId(row.turn_id) if row.turn_id else None,
        # The column admits NULL because SQLite has no way to say a REAL is
        # always written; the writer always writes one (see EntryWriter).
        occurred_at=row.occurred_at or 0.0,
        summary=row.summary,
        body=decode_document(body_type, row.payload),
        cursor=row.cursor,
    )
