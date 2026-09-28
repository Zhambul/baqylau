# Copyright (c) 2026 Zhambyl Yermagambet
"""Answer observer pages, pending scopes, and manager reads with fixed values over real storage."""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

from extensions.models import interpretation_reads, interpretations
from repository.impl.sqlite.extension_observers import SqliteExtensionObserverRepository
from tests.extension_api import observer_samples

if TYPE_CHECKING:
    from baqylau_extension_api.models import scopes

    from domain.ids import CanonicalEventId
    from extensions.models.manager import ManagerSnapshot
    from repository.contract.pending_scope_query import PendingScopeQuery
    from repository.impl.sqlite.connection import SqliteDatabase

HISTORY_REVISION = "default"
GENERATION = "default"
COMMIT_CURSOR = 7


class FixedScopeObservers(SqliteExtensionObserverRepository):
    """Report one fixed scope until its page is observed, with an optional fixed cause depth."""

    def __init__(self, database: SqliteDatabase, scope: scopes.ExtensionScope) -> None:
        """Store the real database, the fixed scope, and no fixed depth."""
        super().__init__(database)
        self.scope = scope
        self.depth: int | None = None

    def pending_scopes(self, pending_scope_query: PendingScopeQuery) -> tuple[scopes.ExtensionScope, ...]:
        """Return the one fixed scope until its facts are observed.

        Returns:
            The fixed scope tuple, or nothing after the fixed page.

        """
        cursor = self.committed_cursor(pending_scope_query.owner, self.scope, HISTORY_REVISION, GENERATION)
        return () if cursor >= COMMIT_CURSOR else (self.scope,)

    def cause_depth(self, event_id: CanonicalEventId, history_revision: str, limit: int) -> int:
        """Return the fixed depth, or the real stored depth.

        Returns:
            The cause depth.

        """
        if self.depth is not None:
            return self.depth
        return super().cause_depth(event_id, history_revision, limit)


@dataclass(frozen=True)
class FakeFacts:
    """Return one fixed committed page for the fixture scope."""

    page: interpretation_reads.CanonicalPage

    def facts_for_scope(
        self, _history_revision: str, _scope: scopes.ExtensionScope, _after_cursor: int, _limit: int,
    ) -> interpretation_reads.CanonicalPage:
        """Return the fixed page.

        Returns:
            The fixed page.

        """
        return self.page


@dataclass(frozen=True)
class FakeManager:
    """Report one active runtime and the manager which committed it."""

    runtime_revision: str
    manager_id: str

    def read_state(self) -> ManagerSnapshot:
        """Return the fixed manager state.

        Returns:
            The fixed state.

        """
        lifecycle = SimpleNamespace(manager_id=self.manager_id)
        return cast("ManagerSnapshot", SimpleNamespace(active_runtime=self.runtime_revision, lifecycle=lifecycle))


def a_page(event_type: str) -> interpretation_reads.CanonicalPage:
    """Build one committed page with the fixture trigger of one event type.

    Returns:
        The committed page.

    """
    event = observer_samples.request().event
    stored = interpretations.StoredCanonicalFact(
        fact=event.fact.model_copy(update={"event_type": event_type}),
        cursor=COMMIT_CURSOR,
        accepted_at=event.accepted_at,
        history_revision=HISTORY_REVISION,
    )
    return interpretation_reads.CanonicalPage(history_revision=HISTORY_REVISION, head=COMMIT_CURSOR, facts=(stored,))
