# Copyright (c) 2026 Zhambyl Yermagambet
"""Commit observer job insertion with its cursor, and its outcome with its output."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from baqylau_extension_api.models.scopes import ExtensionScope

from extensions.models.observations import ObservationAppend
from repository.contract.extension_jobs import ExtensionJob, JobStateChange, ObserverJobRequest

if TYPE_CHECKING:
    from domain.ids import CanonicalEventId
    from repository.contract.pending_scope_query import PendingScopeQuery


@dataclass(frozen=True)
class ObserverCursor:
    """Name one owner's observer progress in one scope, history, and generation."""

    owner: str
    scope: ExtensionScope
    history_revision: str
    generation: str
    commit_cursor: int


@dataclass(frozen=True)
class ObserverAcceptance:
    """Keep one observer cause, its job, and its consumer cursor together."""

    cursor: ObserverCursor
    job: ObserverJobRequest


@dataclass(frozen=True)
class ObserverSettlement:
    """Keep one observer job's final state and its new recorded input together."""

    change: JobStateChange
    observations: ObservationAppend | None = None


class ExtensionObserverRepository(Protocol):
    """Own observer cursors and the accepted observer jobs."""

    def committed_cursor(
        self, owner: str, scope: ExtensionScope, history_revision: str, generation: str,
    ) -> int:
        """Return the last committed observer cursor, or zero."""
        ...

    def ensure_floor(self, owner: str, history_revision: str, generation: str) -> None:
        """Start the owner at the canonical head on its first live pass; keep an existing floor."""
        ...

    def pending_scopes(self, pending_scope_query: PendingScopeQuery) -> tuple[ExtensionScope, ...]:
        """Read the declared scopes with facts after this owner's cursor, oldest head first."""
        ...

    def accept(self, observer_acceptance: ObserverAcceptance) -> ExtensionJob:
        """Store one observer job and advance its cursor in one transaction."""
        ...

    def advance(self, observer_cursor: ObserverCursor) -> None:
        """Advance the cursor past facts that the observer does not select."""
        ...

    def cause_depth(self, event_id: CanonicalEventId, history_revision: str, limit: int) -> int:
        """Count the observer steps behind one fact, at most the limit.

        One step is a fact interpreted from an extension observation that names
        an earlier fact as its cause.
        """
        ...

    def settle(self, observer_settlement: ObserverSettlement) -> ExtensionJob:
        """Store the final job state and its new observations in one transaction.

        A stale job revision or a rejected observation stores nothing.
        """
        ...
