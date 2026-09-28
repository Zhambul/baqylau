# Copyright (c) 2026 Zhambyl Yermagambet
"""Accept durable observer jobs for new committed facts, with each consumer cursor."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import uuid4

from domain.ids import CanonicalEventId, ExtensionJobId
from extensions import (
    observer_calls,
    observer_packages,
    pass_health,
    processing_selection as selection,
    projection_models,
)
from repository.contract import extension_jobs, extension_observers, pending_scope_query

if TYPE_CHECKING:
    from collections.abc import Sequence

    from baqylau_extension_api.models.canonical import CommittedFact
    from baqylau_extension_api.models.scopes import ExtensionScope

    from extensions.models.interpretations import StoredCanonicalFact
    from extensions.registry_package import RegistryPackage

OBSERVER_BATCH_SIZE = 500
# Each owner may have at most this many accepted or running jobs; the pass waits for room.
MAX_OPEN_JOBS = 1000


@dataclass(frozen=True)
class ObserverPass:
    """Accept one job for each selected new fact; the job executor runs it later."""

    facts: projection_models.ProjectionFacts
    observers: extension_observers.ExtensionObserverRepository
    history_revision: str = "default"
    generation: str = "default"
    batch_size: int = OBSERVER_BATCH_SIZE
    jobs: extension_jobs.ExtensionJobRepository | None = None
    max_open_jobs: int = MAX_OPEN_JOBS

    def run_selected(
        self,
        registry_packages: Sequence[RegistryPackage],
        limit: int,
        health: pass_health.PassHealth,
    ) -> int:
        """Accept jobs, for every enabled observer package, in the declared scopes after its own cursor.

        A package's first pass starts it at the canonical head, so an enabled
        observer never acts on facts from before its activation.

        Returns:
            The number of committed facts read across packages.

        """
        return sum(
            self._observe_package(package, limit, health)
            for package in observer_packages.observer_packages(registry_packages)
        )

    def run(self, package: observer_packages.ObserverPackage, scope: ExtensionScope, room: int | None = None) -> int:
        """Accept one job for each selected fact after the package's cursor.

        A fact that the observer does not select makes no job, but the cursor
        still moves past it. At most `room` facts are read, so the owner's
        open jobs cannot pass the limit; the unread facts stay durable.

        Returns:
            The number of facts read.

        """
        cursor = self.observers.committed_cursor(package.extension_id, scope, self.history_revision, self.generation)
        page = self.facts.facts_for_scope(self.history_revision, scope, cursor, self._page_size(room))
        if not page.facts:
            return 0
        observed = _observed(package, scope, page.facts)
        for stored in observed:
            self._accept(package, scope, stored.committed())
        last = page.facts[-1].cursor
        if not observed or observed[-1].cursor != last:
            self.observers.advance(self._cursor(package.extension_id, scope, last))
        return len(page.facts)

    def _observe_package(
        self, package: observer_packages.ObserverPackage, limit: int, health: pass_health.PassHealth,
    ) -> int:
        self.observers.ensure_floor(package.extension_id, self.history_revision, self.generation)
        pending = self.observers.pending_scopes(pending_scope_query.PendingScopeQuery(
            owner=package.extension_id,
            scope_kinds=selection.declared_scope_kinds(package.manifest, selection.FactCapability.OBSERVER),
            history_revision=self.history_revision,
            generation=self.generation,
            limit=limit,
        ))
        total = 0
        for scope in pending:
            room = self._room(package.extension_id)
            if room is not None and room <= 0:
                break
            try:
                total += self.run(package, scope, room)
            except Exception:  # noqa: BLE001 -- Record one observer failure and continue others.
                health.failed(package.extension_id)
            else:
                health.succeeded(package.extension_id)
        return total

    def _room(self, owner: str) -> int | None:
        """Count the free places of the owner's job queue.

        Returns:
            The free places, or None when no job store bounds the queue.

        """
        if self.jobs is None:
            return None
        return self.max_open_jobs - self.jobs.open_jobs(owner)

    def _page_size(self, room: int | None) -> int:
        return self.batch_size if room is None else min(self.batch_size, room)

    def _cursor(self, owner: str, scope: ExtensionScope, commit_cursor: int) -> extension_observers.ObserverCursor:
        return extension_observers.ObserverCursor(
            owner=owner,
            scope=scope,
            history_revision=self.history_revision,
            generation=self.generation,
            commit_cursor=commit_cursor,
        )

    def _accept(self, package: observer_packages.ObserverPackage, scope: ExtensionScope, fact: CommittedFact) -> None:
        target = observer_calls.ObservationTarget(scope, self.history_revision, uuid4().hex, fact)
        request = observer_calls.observation_request(package, target)
        self.observers.accept(extension_observers.ObserverAcceptance(
            cursor=self._cursor(package.extension_id, scope, fact.cursor),
            job=extension_jobs.ObserverJobRequest(
                owner=package.extension_id,
                scope=scope,
                job_id=ExtensionJobId(request.binding.job_id),
                cause_event_id=CanonicalEventId(fact.fact.event_id),
                binding=request.binding.model_dump_json(),
                request=request.model_dump_json(),
                consumer_cursor=fact.cursor,
            ),
        ))


def _observed(
    package: observer_packages.ObserverPackage, scope: ExtensionScope, facts: Sequence[StoredCanonicalFact],
) -> Sequence[StoredCanonicalFact]:
    observer_selection = selection.scope_selection(package.manifest, selection.FactCapability.OBSERVER, scope.kind)
    return selection.selected_facts(observer_selection, facts)
