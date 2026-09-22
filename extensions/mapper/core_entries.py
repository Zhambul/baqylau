# Copyright (c) 2026 Zhambyl Yermagambet
"""Map core feed envelopes without transferring commit authority to workers."""

from baqylau_extension_api.core.entries import CoreSessionEntry

from domain import ids
from domain.entries import SessionEntry
from extensions.mapper import core_entry_bodies


def public_entry(entry: SessionEntry) -> CoreSessionEntry:
    """Separate the stored row body from its host-owned cursor.

    Returns:
        A typed feed proposal with all source and display fields intact.

    """
    return CoreSessionEntry(
        entry_id=entry.entry_id, session_id=entry.session_id, actor_id=entry.actor_id,
        parent_actor_id=entry.parent_actor_id, turn_id=entry.turn_id, occurred_at=entry.occurred_at,
        summary=entry.summary, body=core_entry_bodies.public_body(entry.body),
    )


def private_entry(entry: CoreSessionEntry) -> SessionEntry:
    """Restore a checked proposal with no stored cursor.

    Returns:
        A private feed row for the host to validate and commit.

    """
    checked = CoreSessionEntry.model_validate(entry)
    return SessionEntry(
        entry_id=ids.CanonicalEventId(checked.entry_id), session_id=ids.SessionId(checked.session_id),
        actor_id=ids.ActorId(checked.actor_id),
        parent_actor_id=None if checked.parent_actor_id is None else ids.ActorId(checked.parent_actor_id),
        turn_id=None if checked.turn_id is None else ids.TurnId(checked.turn_id),
        occurred_at=checked.occurred_at, summary=checked.summary, body=core_entry_bodies.private_body(checked.body),
    )
