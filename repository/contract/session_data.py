"""The read model: the aggregate, the feed, and the one write that commits both.

Everything the frontends see lives in three tables, and this is the whole door
to them. The write side is a single method — one canonical event's effect on the
aggregate AND on the feed, committed together — because a reader that could
observe half an event would show a message whose actor does not exist yet.

The read side is five statements, all indexed: the snapshot, the entries page,
and the three deltas a stream polls. None of them touches the canonical log.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from domain.entries import SessionEntry
from domain.ids import SessionId
from domain.sessiondata import ActorFacts, SessionData, SessionFacts


@dataclass(frozen=True)
class SessionDataChanges:
    """One event's whole effect on the read model.

    The writers do not write. Each returns its piece of this, the loop collects
    them, and `apply` commits the lot under one revision — which is what makes
    "everything after cursor C" answerable across both kinds of change.
    """

    entry: SessionEntry | None = None
    session: SessionFacts | None = None
    actors: tuple[ActorFacts, ...] = ()

    @property
    def empty(self) -> bool:
        return self.entry is None and self.session is None and not self.actors


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
        return self.session is None and not self.actors and not self.entries


@dataclass(frozen=True)
class AggregateDelta:
    """The changed aggregate rows, across both tables — what the global stream
    sends. Rows, not whole aggregates: a session whose one actor changed should
    not re-send the other nine, and every row names the session it belongs to.
    """

    sessions: tuple[SessionFacts, ...]
    actors: tuple[ActorFacts, ...]
    cursor: int

    @property
    def empty(self) -> bool:
        return not self.sessions and not self.actors


@dataclass(frozen=True)
class EntryPage:
    """One page of the feed, oldest first.

    `oldest_cursor` is where the NEXT page back starts from, and `has_more` says
    whether there is one — both read in the same transaction as the items, so a
    page cannot disagree with itself.
    """

    items: tuple[SessionEntry, ...]
    oldest_cursor: int
    has_more: bool


class SessionDataRepository(Protocol):
    # --- the write side ------------------------------------------------------

    def apply(
        self,
        session_id: SessionId,
        changes: SessionDataChanges,
        canonical_cursor: int,
    ) -> int:
        """One event's read-model rows and the progress mark, in ONE transaction.

        Stamps every row with the same new revision and returns it. Advancing
        `reaction_progress` inside the same transaction is what makes a crash
        replayable: the mark moves only if the rows did, and re-applying an event
        is harmless because an entry's id is UNIQUE.
        """
        ...

    def progress(self) -> int:
        """The canonical cursor of the last fully processed event; 0 when none."""
        ...

    def clear(self) -> None:
        """Empty the read model and reset the progress mark — the first half of a
        rebuild, whose second half is replaying the writers over the log."""
        ...

    # --- the read side -------------------------------------------------------

    def read(self, session_id: SessionId) -> SessionData | None:
        """One session's aggregate plus its high-water cursor, in one read.

        The cursor has to come from the same transaction as the rows: it is the
        boundary a stream starts from, and one read later it would already be
        describing a different instant.
        """
        ...

    def visible(self) -> tuple[SessionData, ...]:
        """Every session's aggregate — the list view, one query per table."""
        ...

    def entries_page(
        self,
        session_id: SessionId,
        *,
        at: int | None = None,
        before: int | None = None,
        limit: int = 200,
    ) -> EntryPage:
        """The newest `limit` entries at or before a cursor.

        `at` reads the page as of a snapshot's cursor, so the page and the
        snapshot describe one instant; `before` pages further back.
        """
        ...

    def entries_of_types(
        self,
        session_id: SessionId,
        entry_types: Sequence[str],
    ) -> tuple[SessionEntry, ...]:
        """Every entry of these kinds, oldest first.

        The one read that is not a page: a caller that needs the whole history of
        one narrow kind — every prompt, every attention — and would otherwise
        page the entire feed to find it.
        """
        ...

    def pending_attention(self, session_id: SessionId) -> tuple[SessionEntry, ...]:
        """The questions and plans this session is still waiting on, oldest first.

        The one read that answers "is somebody being asked something?" — for the
        notifier, for the control gestures that answer one, and for the dialog
        that validates against one. Derived from the attention entries in order
        (`domain.entries.pending_attention`), because a stored flag would be a
        second answer to the same question.
        """
        ...

    def delta(self, session_id: SessionId, cursor: int) -> SessionDelta:
        """Everything this session changed after `cursor` — the session row, the
        actor rows, the new entries — in ONE transaction.

        One read rather than three, so a frame cannot show an entry whose actor
        arrived in a later transaction, and so the caller is told the cursor it
        reached (see `SessionDelta.cursor`).
        """
        ...

    def changed_after(self, cursor: int) -> AggregateDelta:
        """Every aggregate row that changed after `cursor`, across both tables —
        the global stream, which drives the list and the tab colours."""
        ...
