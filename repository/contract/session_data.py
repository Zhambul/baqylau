# Copyright (c) 2026 Zhambyl Yermagambet
"""The read model: the aggregate, the feed, and the one write that commits both.

Everything the frontends see lives in three tables, and this is the whole door
to them. The write side is a single method — one canonical event's effect on the
aggregate AND on the feed, committed together — because a reader that could
observe half an event would show a message whose actor does not exist yet.

The read side is five statements, all indexed: the snapshot, the entries page,
and the three deltas a stream polls. None of them touches the canonical log.
"""

from __future__ import annotations

from dataclasses import dataclass

from baqylau_extension_api.models import record_changes

from domain.actor_state import ActorFacts
from domain.entries import SessionEntry
from domain.session_state import SessionFacts
from repository.contract.session_data_protocols import (
    SessionDataAggregateRead,
    SessionDataEntryRead,
    SessionDataWrite,
)


@dataclass(frozen=True)
class SessionDataChanges:
    """One event's whole effect on the read model.

    The writers do not write. Each returns its piece of this, the loop collects
    them, and `apply` commits the lot under one revision — which is what makes
    "everything after cursor C" answerable across both kinds of change.
    """

    entries: tuple[SessionEntry, ...] = ()
    records: tuple[record_changes.RecordChange, ...] = ()
    session: SessionFacts | None = None
    actors: tuple[ActorFacts, ...] = ()

    @property
    def empty(self) -> bool:
        """Whether the page is empty."""
        return not any((self.entries, self.records, self.session, self.actors))


@dataclass(frozen=True)
class SessionDelta:
    """What one session changed after a cursor, and how far the reading got.

    The `cursor` is the whole reason this is one object and not three reads: a
    stream's frame id has to be the highest revision it SAW, and an aggregate
    row's revision is a column rather than part of what it holds — so a caller
    given only rows could never advance past an aggregate-only change, and would
    re-send it every quarter second forever.
    """

    session: SessionFacts | None
    actors: tuple[ActorFacts, ...]
    entries: tuple[SessionEntry, ...]
    cursor: int

    @property
    def empty(self) -> bool:
        """Whether the delta is empty."""
        return self.session is None and not self.actors and not self.entries


@dataclass(frozen=True)
class AggregateDelta:
    """Represent aggregate delta.

    The changed aggregate rows, across both tables — what the global stream
        sends. Rows, not whole aggregates: a session whose one actor changed should
        not re-send the other nine, and every row names the session it belongs to.
    """

    sessions: tuple[SessionFacts, ...]
    actors: tuple[ActorFacts, ...]
    cursor: int

    @property
    def empty(self) -> bool:
        """Whether the aggregate delta is empty."""
        return not self.sessions and not self.actors


@dataclass(frozen=True)
class SessionLead:
    """The session row and its named lead actor, without child actor rows."""

    session: SessionFacts
    lead: ActorFacts | None


@dataclass(frozen=True)
class EntryPage:
    """One page of the feed, oldest first.

    `oldest_cursor` is where the NEXT page back starts from, and `has_more` says
    whether there is one — both read in the same transaction as the items, so a
    page cannot disagree with itself.
    """

    entries: tuple[SessionEntry, ...]
    oldest_cursor: int
    has_more: bool


class SessionDataRepository(
    SessionDataWrite,
    SessionDataAggregateRead,
    SessionDataEntryRead,
):
    """Represent the complete session-data repository."""
