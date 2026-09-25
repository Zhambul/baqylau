# Copyright (c) 2026 Zhambyl Yermagambet
"""Map one event's core rows to public projection changes, and the transformed changes back."""

from __future__ import annotations

from typing import TYPE_CHECKING

from baqylau_extension_api.core.aggregate import CoreAggregateState
from baqylau_extension_api.models.projection_changes import (
    CoreActorChange,
    CoreEntryChange,
    CoreSessionChange,
    ExtensionEntryChange,
    ExtensionRecordChange,
    ProjectionChange,
)

from extensions.mapper import core_aggregates, core_entries
from extensions.models.interpretations import StoredCanonicalFact
from extensions.projection_changes import session_entry
from repository.contract.session_data import SessionDataChanges

if TYPE_CHECKING:
    from collections.abc import Sequence

    from baqylau_extension_api.models.canonical import CommittedFact

    from domain import entries as domain_entries
    from engine.sessiondata.contract import AggregateState


def proposed_changes(changes: SessionDataChanges, source_event_id: str) -> tuple[ProjectionChange, ...]:
    """Tag one event's core proposal with stable change identities and its cause.

    Returns:
        The session change, then actor changes, then feed changes, in their order.

    """
    session_facts = changes.session
    session = () if session_facts is None else (CoreSessionChange(
        change_id=f"core:session:{session_facts.session_id}",
        session=core_aggregates.public_session(session_facts),
    ),)
    actors = tuple(
        CoreActorChange(change_id=f"core:actor:{actor.actor_id}", actor=core_aggregates.public_actor(actor))
        for actor in changes.actors
    )
    entries = tuple(
        CoreEntryChange(
            change_id=f"core:entry:{entry.entry_id}", source_event_id=source_event_id,
            entry=core_entries.public_entry(entry),
        )
        for entry in changes.entries
    )
    return (*session, *actors, *entries)


def before_core(before: AggregateState) -> CoreAggregateState:
    """Capture the session and actor rows before this event.

    Returns:
        The public aggregate state that core rules compare against.

    """
    return CoreAggregateState(
        session=None if before.session is None else core_aggregates.public_session(before.session),
        actors=tuple(core_aggregates.public_actor(actor) for actor in before.actors.values()),
    )


def committed_core_changes(
    committed: CommittedFact, history_revision: str, proposal: Sequence[ProjectionChange],
) -> SessionDataChanges:
    """Map the transformed proposal back to the rows that one transaction commits.

    Returns:
        The session, actor, feed, and record changes, with feed rows in proposal order.

    """
    stored = StoredCanonicalFact(
        fact=committed.fact, cursor=committed.cursor, accepted_at=committed.accepted_at,
        history_revision=history_revision,
    )
    session = next((change.session for change in proposal if isinstance(change, CoreSessionChange)), None)
    return SessionDataChanges(
        session=None if session is None else core_aggregates.private_session(session),
        actors=tuple(
            core_aggregates.private_actor(change.actor) for change in proposal if isinstance(change, CoreActorChange)
        ),
        entries=tuple(
            _entry(stored, change) for change in proposal if isinstance(change, CoreEntryChange | ExtensionEntryChange)
        ),
        records=tuple(change.write for change in proposal if isinstance(change, ExtensionRecordChange)),
    )


def _entry(
    stored: StoredCanonicalFact, change: CoreEntryChange | ExtensionEntryChange,
) -> domain_entries.SessionEntry:
    if isinstance(change, CoreEntryChange):
        return core_entries.private_entry(change.entry)
    return session_entry(change.owner, change.scope, (stored,), change)
