# Copyright (c) 2026 Zhambyl Yermagambet
"""Report the extension work that the engine has not done yet, for a test signoff."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from extensions import observer_packages, processing_selection as selection, projector_packages
from extensions.observer_pass import ObserverPass
from extensions.projection_pass import ProjectionPass
from repository.contract.extension_jobs import ExtensionJobRepository
from repository.contract.pending_scope_query import PendingScopeQuery

if TYPE_CHECKING:
    from collections.abc import Sequence

    from baqylau_extension_api.manifest.package import ExtensionManifest

    from extensions.registry_package import RegistryPackage

# One pending scope is enough to know that an owner has work.
FIRST_SCOPE = 1


@dataclass(frozen=True)
class ExtensionBacklog:
    """Name the owners with facts after their cursors, and count the open jobs."""

    projection_owners: tuple[str, ...]
    observer_owners: tuple[str, ...]
    open_jobs: int

    @property
    def empty(self) -> bool:
        """Tell if the engine has done all extension work."""
        return not (self.projection_owners or self.observer_owners or self.open_jobs)


@dataclass(frozen=True)
class BacklogReader:
    """Read the backlog with the same cursors and scope query as the engine passes."""

    projections: ProjectionPass
    observers: ObserverPass
    jobs: ExtensionJobRepository

    def read(self, registry_packages: Sequence[RegistryPackage]) -> ExtensionBacklog:
        """Read the backlog of the active packages.

        Returns:
            The owners with pending facts and the number of accepted or running jobs.

        """
        projection_owners = tuple(
            package.extension_id for package in projector_packages.projector_packages(registry_packages)
            if self._projection_pending(package.extension_id, package.manifest)
        )
        observer_owners = tuple(
            package.extension_id for package in observer_packages.observer_packages(registry_packages)
            if self._observer_pending(package.extension_id, package.manifest)
        )
        open_jobs = sum(self.jobs.open_jobs(package.manifest.extension_id) for package in registry_packages)
        return ExtensionBacklog(projection_owners, observer_owners, open_jobs)

    def _projection_pending(self, owner: str, manifest: ExtensionManifest) -> bool:
        heads = self.projections.heads
        generation = self.projections.generation if heads is None else heads.active_generation(owner)
        query = PendingScopeQuery(
            owner=owner,
            scope_kinds=selection.declared_scope_kinds(manifest, selection.FactCapability.PROJECTOR),
            history_revision=self.projections.history_revision,
            generation=generation,
            limit=FIRST_SCOPE,
        )
        return bool(self.projections.store.pending_scopes(query))

    def _observer_pending(self, owner: str, manifest: ExtensionManifest) -> bool:
        query = PendingScopeQuery(
            owner=owner,
            scope_kinds=selection.declared_scope_kinds(manifest, selection.FactCapability.OBSERVER),
            history_revision=self.observers.history_revision,
            generation=self.observers.generation,
            limit=FIRST_SCOPE,
        )
        return bool(self.observers.observers.pending_scopes(query))
