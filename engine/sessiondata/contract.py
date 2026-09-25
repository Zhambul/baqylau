# Copyright (c) 2026 Zhambyl Yermagambet
"""What a writer is: one concern of the aggregate, folded one event at a time.

A writer does not write. It takes the aggregate as it stands and returns the
aggregate as the event leaves it, and the loop commits the difference — every
row of one event under one revision, in one transaction. That is what makes the
read model safe to poll: no reader can observe half an event.

Pure folds, so a rebuild is the same code as a live tick. The only difference
between them is which reactions run beside the writers, and that is the loop's
business, not a writer's.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Protocol

from domain.actor_state import ActorFacts
from domain.ids import ActorId
from domain.session_state import SessionFacts

if TYPE_CHECKING:
    from collections.abc import Sequence

    from domain.entries import SessionEntry
    from domain.event_base import CanonicalEvent, EventPayload
    from domain.ids import SessionId
    from repository.contract.session_data import SessionDataChanges


@dataclass(frozen=True)
class AggregateState:
    """One session's aggregate, mid-fold.

    Absent rather than empty when nothing has been seen: a session with no
    `session.started` has no aggregate at all, and inventing one would put a
    row on the list for a session nobody can name.
    """

    session: SessionFacts | None = None
    actors: Mapping[ActorId, ActorFacts] = field(default_factory=dict)

    def actor(self, actor_id: ActorId) -> ActorFacts | None:
        """Return the actor.

        Returns:
            Actor.

        """
        return self.actors.get(actor_id)

    def with_actor(self, actor_facts: ActorFacts) -> AggregateState:
        """Return the with actor.

        Returns:
            With actor.

        """
        actors = dict(self.actors)
        actors[actor_facts.actor_id] = actor_facts
        return replace(self, actors=actors)

    def with_actors(self, actors: Mapping[ActorId, ActorFacts]) -> AggregateState:
        """Return the with actors.

        Returns:
            With actors.

        """
        merged = dict(self.actors)
        merged.update(actors)
        return replace(self, actors=merged)


class SessionDataWriter(Protocol):
    """One concern of the aggregate. Sees every accepted event, in order."""

    def write(
        self,
        canonical_event: CanonicalEvent[EventPayload],
        aggregate_state: AggregateState,
    ) -> AggregateState:
        """Write write."""
        ...


class SessionEntryWriter(Protocol):
    """The one appender: a feed-worthy event becomes its ordered immutable rows.

    Its own shape because it is the one writer that does not fold. Nothing it
    produces is ever revised, so there is no state for it to carry. One event
    can produce several entries; their order is the order of the tuple.
    """

    def entries(self, canonical_event: CanonicalEvent[EventPayload]) -> tuple[SessionEntry, ...]:
        """Return the entries."""
        ...


class AppliedActorListener(Protocol):
    """Told what one event's `apply` COMMITTED, once it has committed.

    Not a reaction to an event, and shaped so it cannot pretend to be one: what
    it reacts to is the aggregate CHANGE, which does not exist until the writers
    have folded and the transaction has closed. The one listener today paints a
    terminal tab from the actor's status — and running that beside the reactions,
    before the status was written, would paint the state before the one it is
    painting.

    Handed the rows themselves rather than a session id, so nothing has to read
    back what was just written.
    """

    def applied(self, session_id: SessionId, actors: Sequence[ActorFacts]) -> None:
        """Return the applied."""
        ...


class CoreChangeTransform(Protocol):
    """Let enabled extensions change one event's proposed core rows before they commit.

    The proposal and the result go into the same single transaction, so no
    reader can see a core row that a transform then changes. A transform that
    fails or is rejected leaves the proposal from before it.
    """

    def transform(
        self,
        canonical_event: CanonicalEvent[EventPayload],
        before_aggregate_state: AggregateState,
        proposed_session_data_changes: SessionDataChanges,
    ) -> SessionDataChanges:
        """Return the changes to commit for this event."""
        ...
