# Copyright (c) 2026 Zhambyl Yermagambet
"""Map one checked projector result to host-owned entries and record changes."""

from __future__ import annotations

from typing import TYPE_CHECKING, NoReturn

from baqylau_extension_api.models.projection_changes import (
    ExtensionEntryChange,
    ExtensionRecordChange,
    ProjectionChange,
)

from domain import entries as domain_entries, entry_extensions
from domain.ids import ActorId, CanonicalEventId, SessionId
from repository.contract.session_data import SessionDataChanges

if TYPE_CHECKING:
    from collections.abc import Sequence

    from baqylau_extension_api.models import projections, scopes

    from extensions.models.interpretations import StoredCanonicalFact


def projection_changes(
    owner: str, scope: scopes.ExtensionScope, result: projections.ProjectionResult,
) -> tuple[ProjectionChange, ...]:
    """Build one projector's proposed changes with stable identities.

    Returns:
        The ordered entry changes followed by the record changes.

    """
    entries = tuple(
        ExtensionEntryChange(
            change_id=_change_id("entry", str(projected.source_event_id), projected.entry_key),
            owner=owner,
            scope=scope,
            entry=projected,
        )
        for projected in result.entries
    )
    records = tuple(
        ExtensionRecordChange(
            change_id=_change_id("record", change.key.collection, change.key.key),
            write=change,
        )
        for change in result.record_changes
    )
    return (*entries, *records)


def committed_changes(
    owner: str,
    scope: scopes.ExtensionScope,
    facts: Sequence[StoredCanonicalFact],
    changes: Sequence[ProjectionChange],
) -> SessionDataChanges:
    """Map the final checked changes to stored entries and record changes.

    Returns:
        The complete change set for one projection commit.

    """
    entries = tuple(
        session_entry(owner, scope, facts, change)
        for change in changes
        if isinstance(change, ExtensionEntryChange)
    )
    records = tuple(change.write for change in changes if isinstance(change, ExtensionRecordChange))
    return SessionDataChanges(entries=entries, records=records)


def session_entry(
    owner: str,
    scope: scopes.ExtensionScope,
    facts: Sequence[StoredCanonicalFact],
    change: ExtensionEntryChange,
) -> domain_entries.SessionEntry:
    """Check one extension feed change and map it to a stored session entry.

    Returns:
        The stored session entry.

    """
    if scope.kind != "session":
        _reject("extension entries require a session scope")
    source = _source(facts, str(change.entry.source_event_id))
    if source is None:
        _reject("extension entry names a fact outside its request")
    schema_ref = change.entry.document.schema_ref
    occurred_at = source.accepted_at if change.entry.occurred_at is None else change.entry.occurred_at
    entry_id = _change_id(owner, scope.session_id, change.entry.entry_key)
    return domain_entries.SessionEntry(
        entry_id=CanonicalEventId(entry_id),
        session_id=SessionId(scope.session_id),
        actor_id=ActorId(scope.actor_id),
        parent_actor_id=None,
        turn_id=None,
        occurred_at=occurred_at,
        summary=change.entry.summary,
        body=entry_extensions.ExtensionEntryBody(
            owner=owner,
            entry_type=change.entry.entry_type,
            source_event_id=CanonicalEventId(str(change.entry.source_event_id)),
            schema_ref=entry_extensions.ExtensionSchemaIdentity(
                owner=schema_ref.owner,
                name=schema_ref.name,
                version=schema_ref.version,
                digest=schema_ref.digest,
            ),
            document=change.entry.document.json_text,
        ),
    )


def _change_id(prefix: str, *parts: str) -> str:
    """Join one stable change identity from its parts.

    Returns:
        The joined change identity.

    """
    return ":".join((prefix, *parts))


def _source(facts: Sequence[StoredCanonicalFact], event_id: str) -> StoredCanonicalFact | None:
    """Find the accepted fact a projected row names.

    Returns:
        The matching stored fact, or None.

    """
    for stored in facts:
        if str(stored.fact.event_id) == event_id:
            return stored
    return None


def _reject(message: str) -> NoReturn:
    """Reject one projection row with its exact reason.

    Raises:
        ValueError: Always, with the supplied message.

    """
    raise ValueError(message)
