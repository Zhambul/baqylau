# Copyright (c) 2026 Zhambyl Yermagambet
"""Row DTO to read-model object.

The mirror of `repository/mapper/facts.py` for the three read-model tables: the
identity columns are read as themselves, and the payload column is decoded
against the shape its own `entry_type` names — the same discriminator-then-shape
pass the canonical mapper makes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from domain.actor_state import ActorFacts
from domain.entries import BODY_TYPES, SessionEntry
from domain.ids import ActorId, CanonicalEventId, SessionId, TurnId
from domain.session_state import SessionFacts
from repository.mapper.documents import StoredDocumentError, decode_document

if TYPE_CHECKING:
    from domain.entry_base import EntryBody
    from repository.model.facts import SessionDataActorRow, SessionDataRow, SessionEntryRow


def session_facts(session_data_row: SessionDataRow) -> SessionFacts:
    """Return the session facts.

    Returns:
        Session facts.

    """
    return decode_document(SessionFacts, session_data_row.payload)


def actor_facts(session_data_actor_row: SessionDataActorRow) -> ActorFacts:
    """Return the actor facts.

    Returns:
        Actor facts.

    """
    return decode_document(ActorFacts, session_data_actor_row.payload)


def session_entry(session_entry_row: SessionEntryRow) -> SessionEntry:
    # The column is TEXT, so what it holds is a promise rather than a type: a row
    # naming a kind this build has no body for is drift, and it says so here
    # rather than decoding into whatever shape happens to fit.
    """Return the session entry.

    Returns:
        Session entry.

    Raises:
        StoredDocumentError: If a stored document is not valid.

    """
    body_type: type[EntryBody] | None = next(
        (body for name, body in BODY_TYPES.items() if name == session_entry_row.entry_type),
        None,
    )
    if body_type is None:
        message = f"unknown entry type: {session_entry_row.entry_type!r}"
        raise StoredDocumentError(message)
    return SessionEntry(
        entry_id=CanonicalEventId(session_entry_row.entry_id),
        session_id=SessionId(session_entry_row.session_id),
        actor_id=ActorId(session_entry_row.actor_id),
        parent_actor_id=(ActorId(session_entry_row.parent_actor_id) if session_entry_row.parent_actor_id else None),
        turn_id=TurnId(session_entry_row.turn_id) if session_entry_row.turn_id else None,
        # The column admits NULL because SQLite has no way to say a REAL is
        # always written; the writer always writes one (see EntryWriter).
        occurred_at=session_entry_row.occurred_at or 0,
        summary=session_entry_row.summary,
        body=decode_document(body_type, session_entry_row.payload),
        cursor=session_entry_row.cursor,
        commit_cursor=session_entry_row.commit_cursor,
    )
