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

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Protocol

from domain.entries import SessionEntry
from domain.ids import ActorId, SessionId
from domain.records import CommittedEvent
from domain.sessiondata import ActorFacts, SessionFacts


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
        return dict(self.actors).get(actor_id)

    def with_actor(self, actor_facts: ActorFacts) -> AggregateState:
        actors = dict(self.actors)
        actors[actor_facts.actor_id] = actor_facts
        return replace(self, actors=actors)

    def with_actors(self, actors: Mapping[ActorId, ActorFacts]) -> AggregateState:
        merged = dict(self.actors)
        merged.update(actors)
        return replace(self, actors=merged)


class SessionDataWriter(Protocol):
    """One concern of the aggregate. Sees every accepted event, in order."""

    def write(
        self, committed_event: CommittedEvent, aggregate_state: AggregateState
    ) -> AggregateState:
        ...


class SessionEntryWriter(Protocol):
    """The one appender: a feed-worthy event becomes exactly one immutable row.

    Its own shape because it is the one writer that does not fold. Nothing it
    produces is ever revised, so there is no state for it to carry.
    """

    def entry(self, committed_event: CommittedEvent) -> SessionEntry | None:
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
        ...
