# Copyright (c) 2026 Zhambyl Yermagambet
"""Prepare an accepted complete candidate with fresh, privately owned workers."""

from contextlib import ExitStack
from dataclasses import dataclass
from threading import Event

from baqylau_extension_api.contracts.services import ExtensionHostServices
from baqylau_extension_api.runtime import call_grants, worker_models

from extensions import artifact_contract, prepared_runtime, registry_contract, runtime_plan, worker_contract
from extensions.models.runtime_candidates import MigratingRuntimeSelection, RuntimeCandidate
from extensions.models.runtime_resolution import RuntimeResolution
from extensions.registry_services import RegistryDirectory, RegistryServiceAccess
from extensions.registry_snapshot import RuntimeSnapshot, prepare_snapshot
from extensions.runtime_package_preparation import PreparedPackage, RuntimePackagePreparation, require_running
from extensions.runtime_preparation_contract import (
    ExtensionRuntimePreparation,
    PreparedExtensionRuntime,
    RuntimePreparationError,
)


@dataclass(frozen=True)
class RuntimePreparation(ExtensionRuntimePreparation):
    """Prepare outside the engine thread; callbacks use only the current active set."""

    artifacts: artifact_contract.ExtensionArtifacts
    workers: worker_contract.ExtensionWorkers
    registry: registry_contract.ExtensionRegistry
    ledger: call_grants.HostCallLedger

    def prepare_runtime(
        self, selection: RuntimeCandidate, stop_requested: Event | None = None,
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
            )
            resolution = _resolved(snapshot, selection, packages)
            return prepared_runtime.OwnedPreparedRuntime(snapshot, cleanup.pop_all(), resolution)

    def _prepare_packages(
        self, selection: RuntimeCandidate, plan: tuple[runtime_plan.PreparationPackage, ...],
        cleanup: ExitStack, stop_requested: Event | None,
    ) -> tuple[PreparedPackage, ...]:
        builder = RuntimePackagePreparation(
            self.workers, self._host_services, selection.runtime_revision,
            tuple(schema for package in plan for schema in package.artifact.manifest.schemas), stop_requested,
        )
        return tuple(builder.prepare(package, cleanup) for package in plan)

    def _host_services(self, request: worker_models.WorkerLoadRequest) -> ExtensionHostServices:
        return ExtensionHostServices(
            RegistryDirectory(self.registry), request.environment,
            RegistryServiceAccess(request, self.registry, self.ledger),
        )


def _resolved(
    snapshot: RuntimeSnapshot, selection: RuntimeCandidate, packages: tuple[PreparedPackage, ...],
) -> RuntimeResolution | None:
    if isinstance(selection, MigratingRuntimeSelection):
        resolution = RuntimeResolution(
            runtime=snapshot.runtime_selection(), settings_changes=tuple(
                entry.settings_change for entry in packages if entry.settings_change is not None
            ),
        )
        resolution.validate_candidate(selection)
        return resolution
    if snapshot.runtime_selection() != selection:
        message = "prepared runtime differs from the accepted selection"
        raise RuntimePreparationError(message)
    return None
