# Copyright (c) 2026 Zhambyl Yermagambet
"""Fold one session's facts into its read model with the live writers and no side effects."""

from __future__ import annotations

from typing import TYPE_CHECKING

from baqylau_extension_api.models.canonical import CoreFact

from engine.sessiondata.contract import AggregateState, SessionDataWriter, SessionEntryWriter
from extensions.mapper.core_events import private_committed
from repository.contract.folded_session import FoldedEntry, FoldedSession
from repository.mapper.documents import encode_document

if TYPE_CHECKING:
    from collections.abc import Sequence

    from domain.event_base import CanonicalEvent, EventPayload
    from engine.react.dependencies import ReactionLoopDependencies
    from extensions.models.interpretations import StoredCanonicalFact


def fold_session(
    reaction_loop_dependencies: ReactionLoopDependencies, canonical_events: Sequence[CanonicalEvent[EventPayload]],
) -> FoldedSession:
    """Apply every writer to each fact in cursor order, the same folds that a live pass applies.

    No reaction, listener, or notice runs; the fold only computes rows.

    Returns:
        The session row, the actor rows, and the ordered feed rows with their commit cursors.

    """
    state = _fold_state(reaction_loop_dependencies.writers, canonical_events)
    entries = _fold_entries(reaction_loop_dependencies.session_entry_writer, canonical_events)
    cursor = max((event.cursor or 0 for event in canonical_events), default=0)
    actors = tuple(state.actors.values())
    return FoldedSession(session=state.session, actors=actors, entries=entries, cursor=cursor)


def _fold_state(
    writers: Sequence[SessionDataWriter], canonical_events: Sequence[CanonicalEvent[EventPayload]],
) -> AggregateState:
    state = AggregateState()
    for canonical_event in canonical_events:
        for writer in writers:
            state = writer.write(canonical_event, state)
    return state


def _fold_entries(
    session_entry_writer: SessionEntryWriter, canonical_events: Sequence[CanonicalEvent[EventPayload]],
) -> tuple[FoldedEntry, ...]:
    entries: list[FoldedEntry] = []
    for canonical_event in canonical_events:
        commit_cursor = canonical_event.cursor or 0
        positions = enumerate(session_entry_writer.entries(canonical_event))
        entries.extend(FoldedEntry(commit_cursor, *position) for position in positions)
    return tuple(entries)


def core_events(facts: Sequence[StoredCanonicalFact]) -> tuple[CanonicalEvent[EventPayload], ...]:
    """Select the core facts of a history page as private events.

    Returns:
        The core events in cursor order.

    """
    return tuple(map(private_committed, filter(_is_core, facts)))


def equal_entries(live_folded_session: FoldedSession, candidate_folded_session: FoldedSession) -> int:
    """Count the candidate feed rows that are equal to a live feed row.

    Returns:
        The number of equal rows.

    """
    live_rows = frozenset(_encoded(folded) for folded in live_folded_session.entries)
    return sum(1 for folded in candidate_folded_session.entries if _encoded(folded) in live_rows)


def _is_core(stored: StoredCanonicalFact) -> bool:
    return isinstance(stored.fact, CoreFact)


def _encoded(folded_entry: FoldedEntry) -> bytes:
    return encode_document(folded_entry.entry)
