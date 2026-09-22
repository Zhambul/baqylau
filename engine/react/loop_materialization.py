# Copyright (c) 2026 Zhambyl Yermagambet
"""Provide reaction-loop read-model operations."""

import contextlib
from typing import Protocol

from audit.failures import FailureContext
from domain import actor_state, entries, event_base, events, ids as domain_ids
from engine.react import content_checks
from engine.react.loop_context import ReactionLoopContext
from engine.sessiondata import contract as sessiondata_contract
from repository.contract import session_data


def _event_cursor(canonical_event: event_base.CanonicalEvent[event_base.EventPayload]) -> int:
    if canonical_event.cursor is None:
        msg = "an event with no cursor was handed to the reaction loop"
        raise ValueError(msg)
    return canonical_event.cursor


def _context(canonical_event: event_base.CanonicalEvent[event_base.EventPayload]) -> FailureContext:
    return FailureContext(
        session_id=canonical_event.session_id,
        event_id=canonical_event.event_id,
        cursor=canonical_event.cursor,
    )


def _state(
    repository: session_data.SessionDataRepository,
    session_id: domain_ids.SessionId,
) -> sessiondata_contract.AggregateState:
    stored = repository.read(session_id)
    if stored is None:
        return sessiondata_contract.AggregateState()
    actors = {actor.actor_id: actor for actor in stored.actors}
    return sessiondata_contract.AggregateState(session=stored.session, actors=actors)


def _changed_actors(
    before: sessiondata_contract.AggregateState,
    after: sessiondata_contract.AggregateState,
) -> tuple[actor_state.ActorFacts, ...]:
    known = dict(before.actors)
    changed: list[actor_state.ActorFacts] = []
    for actor_id, actor in after.actors.items():
        if known.get(actor_id) != actor:
            changed.append(actor)
    return tuple(changed)


class _ReactionLoopMaterializationContext(ReactionLoopContext, Protocol):
    def _apply_materialized_event(
        self,
        canonical_event: event_base.CanonicalEvent[event_base.EventPayload],
        states: dict[domain_ids.SessionId, sessiondata_contract.AggregateState],
    ) -> tuple[actor_state.ActorFacts, ...]:
        """Apply one event to the aggregate."""

    def _next_state(
        self,
        canonical_event: event_base.CanonicalEvent[event_base.EventPayload],
        before: sessiondata_contract.AggregateState,
    ) -> sessiondata_contract.AggregateState:
        """Fold one event into an aggregate state."""

    def _audit_empty_body(
        self,
        canonical_event: event_base.CanonicalEvent[event_base.EventPayload],
        session_entry: entries.SessionEntry,
    ) -> None:
        """Record an empty entry body."""

    def _audit_empty_bodies(
        self,
        canonical_event: event_base.CanonicalEvent[event_base.EventPayload],
        session_entries: tuple[entries.SessionEntry, ...],
    ) -> None:
        """Record every empty entry body."""

    def _announce(
        self,
        listeners: tuple[sessiondata_contract.AppliedActorListener, ...],
        session_id: domain_ids.SessionId,
        actors: tuple[actor_state.ActorFacts, ...],
        canonical_event: event_base.CanonicalEvent[event_base.EventPayload],
    ) -> None:
        """Notify listeners after a committed change."""

    def _audit_failure(self, where: str, failure_context: FailureContext) -> None:
        """Record a recoverable failure."""


class ReactionLoopMaterialization:
    """Provide read-model materialization operations."""

    def _materialize(
        self: _ReactionLoopMaterializationContext,
        canonical_event: event_base.CanonicalEvent[event_base.EventPayload],
        states: dict[domain_ids.SessionId, sessiondata_contract.AggregateState],
        listeners: tuple[sessiondata_contract.AppliedActorListener, ...],
    ) -> None:
        session_id = canonical_event.session_id
        try:
            changed_actors = self._apply_materialized_event(canonical_event, states)
        except Exception:
            self._audit_failure("session data", _context(canonical_event))
            raise
        self._announce(listeners, session_id, changed_actors, canonical_event)

    def _apply_materialized_event(
        self: _ReactionLoopMaterializationContext,
        canonical_event: event_base.CanonicalEvent[event_base.EventPayload],
        states: dict[domain_ids.SessionId, sessiondata_contract.AggregateState],
    ) -> tuple[actor_state.ActorFacts, ...]:
        before = states.get(canonical_event.session_id) or _state(
            self.dependencies.session_data_repository,
            canonical_event.session_id,
        )
        after = self._next_state(canonical_event, before)
        entries = self.dependencies.session_entry_writer.entries(canonical_event)
        self._audit_empty_bodies(canonical_event, entries)
        changed_actors = _changed_actors(before, after)
        changes = session_data.SessionDataChanges(
            entries=entries,
            session=None if after.session == before.session else after.session,
            actors=changed_actors,
        )
        self.dependencies.session_data_repository.apply(
            canonical_event.session_id, changes, _event_cursor(canonical_event),
        )
        states[canonical_event.session_id] = after
        return changed_actors

    def _next_state(
        self: _ReactionLoopMaterializationContext,
        canonical_event: event_base.CanonicalEvent[event_base.EventPayload],
        before: sessiondata_contract.AggregateState,
    ) -> sessiondata_contract.AggregateState:
        state = before
        for writer in self.dependencies.writers:
            state = writer.write(canonical_event, state)
        return state

    def _announce(
        self: _ReactionLoopMaterializationContext,
        listeners: tuple[sessiondata_contract.AppliedActorListener, ...],
        session_id: domain_ids.SessionId,
        actors: tuple[actor_state.ActorFacts, ...],
        canonical_event: event_base.CanonicalEvent[event_base.EventPayload],
    ) -> None:
        if not actors:
            return
        for listener in listeners:
            try:
                listener.applied(session_id, actors)
            except Exception:  # noqa: BLE001 -- Audit one listener failure and continue with other listeners.
                self._audit_failure(type(listener).__name__, _context(canonical_event))

    def _audit_empty_bodies(
        self: _ReactionLoopMaterializationContext,
        canonical_event: event_base.CanonicalEvent[event_base.EventPayload],
        session_entries: tuple[entries.SessionEntry, ...],
    ) -> None:
        for session_entry in session_entries:
            self._audit_empty_body(canonical_event, session_entry)

    def _audit_empty_body(
        self: _ReactionLoopMaterializationContext,
        canonical_event: event_base.CanonicalEvent[event_base.EventPayload],
        session_entry: entries.SessionEntry,
    ) -> None:
        if not content_checks.has_empty_required_body(session_entry):
            return
        context = FailureContext(
            session_id=canonical_event.session_id,
            event_id=canonical_event.event_id,
            cursor=canonical_event.cursor,
            entry_id=session_entry.entry_id,
            entry_type=session_entry.entry_type,
            event_type=events.EVENT_TYPES[type(canonical_event.payload)],
        )
        with contextlib.suppress(Exception):
            self.dependencies.audit_recorder.error(
                str(canonical_event.session_id),
                "entry fold (empty body)",
                context,
            )
