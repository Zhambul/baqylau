# Copyright (c) 2026 Zhambyl Yermagambet
"""Small protocols for session-data repository operations."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Sequence

    from domain.entries import SessionEntry
    from domain.ids import SessionId
    from domain.session_state import SessionData
    from repository.contract.session_data import (
        AggregateDelta,
        EntryPage,
        SessionDataChanges,
        SessionDelta,
        SessionLead,
    )


class SessionDataWrite(Protocol):
    """Write session data and manage its progress."""

    def apply(
        self,
        session_id: SessionId,
        session_data_changes: SessionDataChanges,
        canonical_cursor: int,
    ) -> int:
        """Apply one canonical event to the read model."""
        ...

    def progress(self) -> int:
        """Return the last applied canonical cursor."""
        ...

    def advance_past_extensions(self, canonical_cursor: int) -> None:
        """Skip only verified live extension facts, without display changes or a backward cursor."""
        ...

    def clear(self) -> None:
        """Clear the read model and its progress mark."""
        ...


class SessionDataAggregateRead(Protocol):
    """Read session aggregates."""

    def read(self, session_id: SessionId) -> SessionData | None:
        """Read one session aggregate."""
        ...

    def visible(self) -> tuple[SessionData, ...]:
        """Read all visible session aggregates."""
        ...

    def running(self) -> tuple[SessionData, ...]:
        """Read all running session aggregates."""
        ...

    def working_directories(self) -> tuple[str, ...]:
        """Read known working directories."""
        ...

    def lead_sessions(self) -> tuple[SessionLead, ...]:
        """Read each session with its lead actor."""
        ...

    def high_water_cursor(self) -> int:
        """Return the highest read-model cursor."""
        ...


class SessionDataEntryRead(Protocol):
    """Read session entry feeds and deltas."""

    def entries_page(
        self,
        session_id: SessionId,
        *,
        at: int | None = None,
        before: int | None = None,
        limit: int = 200,
    ) -> EntryPage:
        """Read one page of session entries."""
        ...

    def entries_of_types(
        self,
        session_id: SessionId,
        entry_types: Sequence[str],
    ) -> tuple[SessionEntry, ...]:
        """Read all entries of the specified types."""
        ...

    def pending_attention(self, session_id: SessionId) -> tuple[SessionEntry, ...]:
        """Read unanswered session attention."""
        ...

    def delta(self, session_id: SessionId, cursor: int, entry_cursor: int | None = None) -> SessionDelta:
        """Read changes for one session after a cursor."""
        ...

    def changed_after(self, cursor: int) -> AggregateDelta:
        """Read aggregate changes after a cursor."""
        ...
