# Copyright (c) 2026 Zhambyl Yermagambet
"""Name one closed session's candidate history, its comparison, and its switch."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

from pydantic import BaseModel

from domain.ids import SessionId

if TYPE_CHECKING:
    from extensions.models.interpretations import StoredCanonicalFact
    from extensions.models.observations import StoredObservation
    from repository.contract.folded_session import FoldedSession


class ReprocessingState(StrEnum):
    """Show where one candidate history is in its replay and switch."""

    BUILDING = "building"
    READY = "ready"
    SWITCHING = "switching"
    ACTIVE = "active"
    RETIRED = "retired"
    FAILED = "failed"


class ReprocessingRefusedError(RuntimeError):
    """Refuse reprocessing or a switch outside the safe V1 boundary, with the reason."""


class HistoryComparison(BaseModel):
    """Count the live and candidate facts and feed rows of one session and the equal feed rows."""

    live_facts: int
    candidate_facts: int
    live_entries: int
    candidate_entries: int
    equal_entries: int


@dataclass(frozen=True)
class HistoryReprocessing:
    """Keep one candidate history's session, state, replay progress, and comparison."""

    history_revision: str
    session_id: SessionId
    state: ReprocessingState
    replay_cursor: int
    live_head: int
    comparison: HistoryComparison | None = None
    diagnostic: str | None = None


class HistoryReprocessingRepository(Protocol):
    """Own candidate histories of closed sessions and their atomic switch."""

    def create(self, session_id: SessionId) -> HistoryReprocessing:
        """Start one candidate history after the V1 boundary checks."""
        ...

    def read(self, history_revision: str) -> HistoryReprocessing | None:
        """Read one candidate history."""
        ...

    def in_state(self, reprocessing_state: ReprocessingState) -> tuple[HistoryReprocessing, ...]:
        """Read the candidate histories in one state, oldest first."""
        ...

    def advance(self, history_revision: str, replay_cursor: int) -> None:
        """Record the last replayed original input."""
        ...

    def settle(
        self,
        history_revision: str,
        reprocessing_state: ReprocessingState,
        history_comparison: HistoryComparison | None = None,
        diagnostic: str | None = None,
    ) -> None:
        """Record a replayed, failed, or switch-requested candidate."""
        ...

    def switch(self, history_revision: str, folded_session: FoldedSession) -> HistoryReprocessing:
        """Make the candidate the session's default history and keep the previous one for recovery."""
        ...


class SessionHistoryReader(Protocol):
    """Read one session's input and facts across all its actor scopes."""

    def session_observations(
        self, session_id: SessionId, after_cursor: int, limit: int,
    ) -> tuple[StoredObservation, ...]:
        """Read every original input of one session, of all its actors, in arrival order."""
        ...

    def session_facts(self, session_id: SessionId, history_revision: str) -> tuple[StoredCanonicalFact, ...]:
        """Read every fact of one session in one history, in cursor order."""
        ...


@dataclass(frozen=True)
class HistoryStores:
    """Give the engine the candidate store and the session reader together."""

    candidates: HistoryReprocessingRepository
    sessions: SessionHistoryReader
