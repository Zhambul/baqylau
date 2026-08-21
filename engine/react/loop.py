"""The reaction loop: committed facts in, world and read model out.

The second of the daemon's two loops, and the one that does everything a fact
CAUSES. It is decoupled from translation by the canonical store itself — there
is no queue between them, only the cursor: the interpreter appends facts, this
follows them, and neither waits on the other. A reaction that is slow or broken
can no longer stall the evidence pipeline, which is the whole reason for the
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
from domain.ids import SessionId
from domain.records import CommittedEvent
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
        canonical_events: CanonicalEventRepository,
        session_data: SessionDataRepository,
        reactions: tuple[CanonicalEventReaction, ...],
        entry_writer: SessionEntryWriter,
        writers: tuple[SessionDataWriter, ...],
        listeners: tuple[AppliedActorListener, ...],
        harnesses: HarnessRegistry,
        controls: HarnessReactorContext,
        audit: AuditRecorder,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.canonical_events = canonical_events
        self.session_data = session_data
        self.reactions = reactions
        self.entry_writer = entry_writer
        self.writers = writers
        self.listeners = listeners
        self.harnesses = harnesses
        self.controls = controls  # handed to harness reactors per call
        self.audit = audit
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
        for committed in events:
            self._react(committed)
            self._materialize(committed, states, self.listeners)
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
            for committed in events:
                # No reactions AND no listeners: both are side effects, and a
                # replay of history must not reopen a pane or repaint the tab of
                # a session that finished days ago.
                self._materialize(committed, states, ())
            total += len(events)

    # --- the side effects ----------------------------------------------------

    def _react(self, committed: CommittedEvent) -> None:
        event = committed.event
        for reaction in self.reactions:
            try:
                reaction.react(event)
            except Exception:
                self._audit_failure(type(reaction).__name__, _context(committed))
        try:
            reactors = self.harnesses.plugin(event.harness).reactors
        except Exception:
            self._audit_failure("harness lookup", _context(committed))
            return
        for reactor in reactors:
            try:
                reactor.react(event, self.controls)
            except Exception:
                self._audit_failure(type(reactor).__name__, _context(committed))

    # --- the read model ------------------------------------------------------

    def _materialize(
        self,
        committed: CommittedEvent,
        states: dict[SessionId, AggregateState],
        listeners: tuple[AppliedActorListener, ...],
    ) -> None:
        """One event's whole effect on the read model, committed as one row set.

        A failure here still advances nothing: the progress mark moves inside
        the same transaction as the rows, so the next tick sees this event again
        — and its entry insert is idempotent, so seeing it twice is harmless.
        """
        session_id = committed.event.session_id
        try:
            before = states.get(session_id) or _state(self.session_data, session_id)
            after = before
            for writer in self.writers:
                after = writer.write(committed, after)
            changes = SessionDataChanges(
                entry=self.entry_writer.entry(committed),
                session=after.session if after.session != before.session else None,
                actors=_changed_actors(before, after),
            )
            self.session_data.apply(session_id, changes, committed.cursor)
            states[session_id] = after
        except Exception:
            self._audit_failure("session data", _context(committed))
            return
        self._announce(listeners, session_id, changes.actors, committed)

    def _announce(
        self,
        listeners: tuple[AppliedActorListener, ...],
        session_id: SessionId,
        actors: tuple[ActorFacts, ...],
        committed: CommittedEvent,
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
                self._audit_failure(type(listener).__name__, _context(committed))

    def _audit_failure(self, where: str, context: dict[str, object]) -> None:
        """Record a swallowed failure, then carry on. Guarded, so a broken
        auditor can never take down the loop it exists to explain."""
        try:
            self.audit.error(
                str(context.get("session_id", "")), f"reactions ({where})", context
            )
        except Exception:
            pass


def _context(committed: CommittedEvent) -> dict[str, object]:
    return {
        "session_id": str(committed.event.session_id),
        "event_id": str(committed.event.event_id),
        "cursor": committed.cursor,
    }


def _state(session_data: SessionDataRepository, session_id: SessionId) -> AggregateState:
    stored = session_data.read(session_id)
    if stored is None:
        return AggregateState()
    return AggregateState(
        session=stored.session,
        actors={actor.actor_id: actor for actor in stored.actors},
    )


def _changed_actors(
    before: AggregateState, after: AggregateState
) -> tuple[ActorFacts, ...]:
    known = dict(before.actors)
    return tuple(
        actor
        for actor_id, actor in dict(after.actors).items()
        if known.get(actor_id) != actor
    )
