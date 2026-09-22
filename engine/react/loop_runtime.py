# Copyright (c) 2026 Zhambyl Yermagambet
"""Provide reaction-loop runtime operations."""

from collections.abc import Callable
from contextlib import ExitStack
from typing import Protocol

from baqylau_extension_api.models.canonical import CoreFact

from audit.failures import FailureContext
from domain import event_base, ids as domain_ids
from engine.react.loop_context import ReactionLoopContext
from engine.sessiondata import contract as sessiondata_contract
from engine.sessiondata.actor_batch import AppliedActorBatch
from extensions.mapper.core_events import private_committed
from extensions.models.interpretations import StoredCanonicalFact

REACTION_BATCH_SIZE = 500


def _notify_actor_batch(
    applied_actor_batch: AppliedActorBatch,
    listeners: tuple[sessiondata_contract.AppliedActorListener, ...],
    audit_failure: Callable[[str, FailureContext], None],
) -> None:
    for session_id, actors in applied_actor_batch.actors.items():
        for listener in listeners:
            try:
                listener.applied(session_id, tuple(actors.values()))
            except Exception:  # noqa: BLE001 - Record each listener failure and continue other listeners.
                audit_failure(type(listener).__name__, FailureContext(session_id=session_id))


class _ReactionLoopRuntimeContext(ReactionLoopContext, Protocol):
    def tick(self, listeners: tuple[sessiondata_contract.AppliedActorListener, ...] | None = None) -> int:
        """React to and materialize one event batch."""

    def _react(self, canonical_event: event_base.CanonicalEvent[event_base.EventPayload]) -> None:
        """Apply side-effect reactions."""

    def _materialize(
        self,
        canonical_event: event_base.CanonicalEvent[event_base.EventPayload],
        states: dict[domain_ids.SessionId, sessiondata_contract.AggregateState],
        listeners: tuple[sessiondata_contract.AppliedActorListener, ...],
    ) -> None:
        """Apply an event to the read model."""

    def _audit_failure(self, where: str, failure_context: FailureContext) -> None:
        """Record a recoverable failure."""

    def _replay_events(self, canonical_facts: tuple[StoredCanonicalFact, ...]) -> None:
        """Materialize core replay facts and skip extension facts without listeners."""


class ReactionLoopRuntime:
    """Provide reaction-loop runtime operations."""

    def tick(
        self: _ReactionLoopRuntimeContext,
        listeners: tuple[sessiondata_contract.AppliedActorListener, ...] | None = None,
    ) -> int:
        """React to and materialize one event batch.

        Returns:
            The number of mixed facts consumed by the core read model.

        """
        page = self.dependencies.canonical_fact_reader.current_fact_page(
            self.dependencies.session_data_repository.progress(), REACTION_BATCH_SIZE,
        )
        states: dict[domain_ids.SessionId, sessiondata_contract.AggregateState] = {}
        applied_listeners = self.dependencies.listeners if listeners is None else listeners
        for stored in page.facts:
            if not isinstance(stored.fact, CoreFact):
                self.dependencies.session_data_repository.advance_past_extensions(stored.cursor)
                continue
            canonical_event = private_committed(stored)
            self._react(canonical_event)
            self._materialize(canonical_event, states, applied_listeners)
        return len(page.facts)

    def drain(self: _ReactionLoopRuntimeContext, cancelled: Callable[[], bool]) -> int:
        """Fold ready history before announcing the final display state.

        Returns:
            The number of processed events across all batches.

        """
        batch = AppliedActorBatch()
        total = 0
        with self.dependencies.changes.batch(), ExitStack() as cleanup:
            cleanup.callback(_notify_actor_batch, batch, self.dependencies.listeners, self._audit_failure)
            while not cancelled():
                count = self.tick((batch,))
                if not count:
                    break
                total += count
        return total

    def rebuild(self: _ReactionLoopRuntimeContext) -> int:
        """Rebuild the read model without side effects.

        Returns:
            The number of stored events used for the rebuild.

        """
        repository = self.dependencies.canonical_fact_reader
        session_data = self.dependencies.session_data_repository
        session_data.clear()
        total = 0
        while True:
            page = repository.current_fact_page(session_data.progress(), REACTION_BATCH_SIZE)
            if not page.facts:
                return total
            self._replay_events(page.facts)
            total += len(page.facts)

    def _replay_events(
        self: _ReactionLoopRuntimeContext,
        canonical_facts: tuple[StoredCanonicalFact, ...],
    ) -> None:
        states: dict[domain_ids.SessionId, sessiondata_contract.AggregateState] = {}
        for stored in canonical_facts:
            if not isinstance(stored.fact, CoreFact):
                self.dependencies.session_data_repository.advance_past_extensions(stored.cursor)
                continue
            canonical_event = private_committed(stored)
            self._materialize(canonical_event, states, ())
