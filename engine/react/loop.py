"""The reaction loop: committed facts in, world and read model out.

The second of the daemon's two loops, and the one that does everything a fact
CAUSES. It is decoupled from translation by the canonical store itself — there
is no queue between them, only the cursor: the interpreter appends facts, this
follows them, and neither waits on the other. A reaction that is slow or broken
can no longer stall the raw event pipeline, which is the whole reason for the
split.

Per event, in order: the side-effect reactions, the harness's own reactors, the
writers — whose result is committed as one transaction together with the progress
mark — and then the listeners, told what that transaction wrote. The order is the
dependency order: a pane anchors to the sessions row the interpreter already
wrote, and anything that shows the aggregate has to come after the aggregate
exists. A tab colour painted before the status was written paints the previous
one.

REPLAY is the same code with the side effects left out. A rebuild drives the
writers over the whole log, and if the reactions rode along with them every
finished session would reopen its panes and re-fire its notifications.
"""

from __future__ import annotations

import threading
import time
from typing import Callable

from audit.recorder import AuditRecorder
from domain.events import CanonicalEvent, EventPayload
from domain.ids import SessionId
from domain.sessiondata import ActorFacts
from engine.sessiondata.contract import (
    AggregateState,
    AppliedActorListener,
    SessionDataWriter,
    SessionEntryWriter,
)
from harness.contract import CanonicalEventReaction, HarnessReactorContext
from harness.registry import HarnessRegistry
from repository.contract.facts import CanonicalEventRepository
from repository.contract.session_data import SessionDataChanges, SessionDataRepository

TICK_INTERVAL_SECONDS = 0.25
REACTION_BATCH_SIZE = 500


class ReactionLoop:
    """One thread, one method: `tick()`.

    Every step is contained and audited. The thread must outlive every failure
    it can observe — nothing restarts it, and a session whose reactions stopped
    looks alive while showing nothing.
    """

    def __init__(
        self,
        canonical_event_repository: CanonicalEventRepository,
        session_data_repository: SessionDataRepository,
        reactions: tuple[CanonicalEventReaction, ...],
        session_entry_writer: SessionEntryWriter,
        writers: tuple[SessionDataWriter, ...],
        listeners: tuple[AppliedActorListener, ...],
        harness_registry: HarnessRegistry,
        harness_reactor_context: HarnessReactorContext,
        audit_recorder: AuditRecorder,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.canonical_events = canonical_event_repository
        self.session_data = session_data_repository
        self.reactions = reactions
        self.entry_writer = session_entry_writer
        self.writers = writers
        self.listeners = listeners
        self.harnesses = harness_registry
        self.controls = harness_reactor_context  # handed to harness reactors per call
        self.audit = audit_recorder
        self.clock = clock

    def run(self, stop_event: threading.Event) -> None:
        while not stop_event.is_set():
            try:
                self.tick()
            except Exception:
                self._audit_failure("tick", {})
            stop_event.wait(TICK_INTERVAL_SECONDS)

    def tick(self) -> int:
        """One batch of facts, reacted to and materialized. Returns how many."""
        events = self.canonical_events.page_from(self.session_data.progress(), REACTION_BATCH_SIZE)
        # One aggregate read per SESSION per batch, not per event: the fold
        # carries forward in memory and every row is written whole, so a batch of
        # four hundred facts about one session reads it once. Dropped at the end
        # of the batch, which is what makes a failed apply self-healing — the
        # next tick reads the committed truth again.
        states: dict[SessionId, AggregateState] = {}
        for canonical_event in events:
            self._react(canonical_event)
            self._materialize(canonical_event, states, self.listeners)
        return len(events)

    def rebuild(self) -> int:
        """Re-derive the whole read model from the log, writers only.

        The insurance against a writer that was wrong or crashed: clear the read
        model and fold every fact again. The side-effect reactions are excluded
        by construction — this method never calls them — because replaying them
        would reopen panes and re-announce work that finished days ago.
        """
        self.session_data.clear()
        total = 0
        while True:
            events = self.canonical_events.page_from(
                self.session_data.progress(), REACTION_BATCH_SIZE
            )
            if not events:
                return total
            states: dict[SessionId, AggregateState] = {}
            for canonical_event in events:
                # No reactions AND no listeners: both are side effects, and a
                # replay of history must not reopen a pane or repaint the tab of
                # a session that finished days ago.
                self._materialize(canonical_event, states, ())
            total += len(events)

    # --- the side effects ----------------------------------------------------

    def _react(self, canonical_event: CanonicalEvent[EventPayload]) -> None:
        for reaction in self.reactions:
            try:
                reaction.react(canonical_event)
            except Exception:
                self._audit_failure(type(reaction).__name__, _context(canonical_event))
        try:
            reactors = self.harnesses.plugin(canonical_event.harness).reactors
        except Exception:
            self._audit_failure("harness lookup", _context(canonical_event))
            return
        for reactor in reactors:
            try:
                reactor.react(canonical_event, self.controls)
            except Exception:
                self._audit_failure(type(reactor).__name__, _context(canonical_event))

    # --- the read model ------------------------------------------------------

    def _materialize(
        self,
        canonical_event: CanonicalEvent[EventPayload],
        states: dict[SessionId, AggregateState],
        listeners: tuple[AppliedActorListener, ...],
    ) -> None:
        """One event's whole effect on the read model, committed as one row set.

        A failure here still advances nothing: the progress mark moves inside
        the same transaction as the rows, so the next tick sees this event again
        — and its entry insert is idempotent, so seeing it twice is harmless.
        """
        session_id = canonical_event.session_id
        try:
            before = states.get(session_id) or _state(self.session_data, session_id)
            after = before
            for writer in self.writers:
                after = writer.write(canonical_event, after)
            changes = SessionDataChanges(
                entry=self.entry_writer.entry(canonical_event),
                session=after.session if after.session != before.session else None,
                actors=_changed_actors(before, after),
            )
            if canonical_event.cursor is None:
                raise ValueError("an event with no cursor was handed to the reaction loop")
            self.session_data.apply(session_id, changes, canonical_event.cursor)
            states[session_id] = after
        except Exception:
            self._audit_failure("session data", _context(canonical_event))
            return
        self._announce(listeners, session_id, changes.actors, canonical_event)

    def _announce(
        self,
        listeners: tuple[AppliedActorListener, ...],
        session_id: SessionId,
        actors: tuple[ActorFacts, ...],
        canonical_event: CanonicalEvent[EventPayload],
    ) -> None:
        """Tell the listeners what committed. After the transaction, deliberately:
        an aggregate change does not exist until it is durable, and a listener
        that acted on one that then failed to commit would be showing a state
        nothing else agrees with."""
        if not actors:
            return
        for listener in listeners:
            try:
                listener.applied(session_id, actors)
            except Exception:
                self._audit_failure(type(listener).__name__, _context(canonical_event))

    def _audit_failure(self, where: str, context: dict[str, object]) -> None:
        """Record a swallowed failure, then carry on. Guarded, so a broken
        auditor can never take down the loop it exists to explain."""
        try:
            self.audit.error(
                str(context.get("session_id", "")), f"reactions ({where})", context
            )
        except Exception:
            pass


def _context(canonical_event: CanonicalEvent[EventPayload]) -> dict[str, object]:
    return {
        "session_id": str(canonical_event.session_id),
        "event_id": str(canonical_event.event_id),
        "cursor": canonical_event.cursor,
    }


def _state(
    session_data_repository: SessionDataRepository, session_id: SessionId
) -> AggregateState:
    stored = session_data_repository.read(session_id)
    if stored is None:
        return AggregateState()
    return AggregateState(
        session=stored.session,
        actors={actor.actor_id: actor for actor in stored.actors},
    )


def _changed_actors(
    before_aggregate_state: AggregateState, after_aggregate_state: AggregateState
) -> tuple[ActorFacts, ...]:
    known = dict(before_aggregate_state.actors)
    return tuple(
        actor
        for actor_id, actor in dict(after_aggregate_state.actors).items()
        if known.get(actor_id) != actor
    )
