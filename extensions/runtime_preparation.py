# Copyright (c) 2026 Zhambyl Yermagambet
"""Prepare an accepted complete candidate with fresh, privately owned workers."""

from contextlib import ExitStack
from dataclasses import dataclass, field
from threading import Event

from baqylau_extension_api.runtime import call_grants

from extensions import (
    artifact_contract,
    prepared_runtime,
    registry_contract,
    runtime_plan,
    service_removal,
    worker_contract,
)
from extensions.models import runtime_candidates, runtime_resolution
from extensions.preparation_services import PreparationServices
from extensions.registry_snapshot import RuntimeSnapshot, prepare_snapshot
from extensions.runtime_package_preparation import PreparedPackage, RuntimePackagePreparation
from extensions.runtime_preparation_contract import (
    ExtensionRuntimePreparation,
    PreparedExtensionRuntime,
    RuntimePreparationError,
    require_running,
)
from extensions.worker_host_services import WorkerHostServices


@dataclass(frozen=True)
class RuntimePreparation(ExtensionRuntimePreparation):
    """Prepare outside the engine thread; callbacks use only the current active set."""

    artifacts: artifact_contract.ExtensionArtifacts
    workers: worker_contract.ExtensionWorkers
    registry: registry_contract.ExtensionRegistry
    ledger: call_grants.HostCallLedger
    services: PreparationServices = field(default_factory=PreparationServices)

    def prepare_runtime(
        self, selection: runtime_candidates.RuntimeCandidate, stop_requested: Event | None = None,
    ) -> PreparedExtensionRuntime:
        """Check the whole candidate, activate in dependency order, and own cleanup.

        Returns:
            Fresh worker resources and their exact checked registry snapshot.

        """
        require_running(stop_requested)
        plan = runtime_plan.preparation_plan(selection, self.artifacts)
        with ExitStack() as cleanup:
            packages = self._prepare_packages(selection, plan, cleanup, stop_requested)
            require_running(stop_requested)
            snapshot = prepare_snapshot(
                selection.catalog_revision, selection.runtime_revision, tuple(entry.package for entry in packages),
                self.services.scope_relations,
            )
            resolution = _resolved(snapshot, selection, packages)
            return prepared_runtime.OwnedPreparedRuntime(snapshot, cleanup.pop_all(), resolution)

    def _prepare_packages(
        self, selection: runtime_candidates.RuntimeCandidate, plan: tuple[runtime_plan.PreparationPackage, ...],
        cleanup: ExitStack, stop_requested: Event | None,
    ) -> tuple[PreparedPackage, ...]:
        builder = RuntimePackagePreparation(
            self.workers, WorkerHostServices(self.registry, self.ledger, self.services).for_worker,
            selection.runtime_revision,
            tuple(schema for package in plan for schema in package.artifact.manifest.schemas), stop_requested,
            self.services.record_migrations,
            service_removal.published_retirements(self.registry, (package.artifact.manifest for package in plan)),
        )
        return tuple(builder.prepare(package, cleanup) for package in plan)


def _resolved(
    snapshot: RuntimeSnapshot, selection: runtime_candidates.RuntimeCandidate, packages: tuple[PreparedPackage, ...],
) -> runtime_resolution.RuntimeResolution | None:
    if isinstance(selection, runtime_candidates.MigratingRuntimeSelection):
        resolution = runtime_resolution.RuntimeResolution(
            runtime=snapshot.runtime_selection(),
            settings_changes=tuple(entry.settings_change for entry in packages if entry.settings_change is not None),
            record_generations=tuple(
                entry.record_generation for entry in packages if entry.record_generation is not None
            ),
        )
        resolution.validate_candidate(selection)
        return resolution
    if snapshot.runtime_selection() != selection:
        message = "prepared runtime differs from the accepted selection"
        raise RuntimePreparationError(message)
    return None
