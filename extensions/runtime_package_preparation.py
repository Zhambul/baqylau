# Copyright (c) 2026 Zhambyl Yermagambet
"""Convert settings before activating the same privately owned candidate worker."""

from collections.abc import Callable
from contextlib import ExitStack, closing
from dataclasses import dataclass
from threading import Event

from baqylau_extension_api.contracts.services import ExtensionHostServices
from baqylau_extension_api.models import directory, environment, lifecycle
from baqylau_extension_api.models.documents import SchemaDefinition
from baqylau_extension_api.runtime.worker_models import WorkerLoadRequest

from extensions import runtime_plan, runtime_settings_migration, worker_contract
from extensions.models import lifecycle_selection, registry, runtime_candidates, settings
from extensions.registry_package import RegistryPackage
from extensions.runtime_preparation_contract import RuntimePreparationError, RuntimePreparationStoppedError


@dataclass(frozen=True)
class PreparedPackage:
    """Keep converted raw overrides with their activated registry entry."""

    package: RegistryPackage
    settings_change: lifecycle_selection.SettingsChange | None = None


@dataclass(frozen=True)
class RuntimePackagePreparation:
    """Share one candidate context without giving feature code storage access."""

    workers: worker_contract.ExtensionWorkers
    services: Callable[[WorkerLoadRequest], ExtensionHostServices]
    runtime_revision: str
    schemas: tuple[SchemaDefinition, ...]
    stop_requested: Event | None

    def prepare(self, source: runtime_plan.PreparationPackage, cleanup: ExitStack) -> PreparedPackage:
        """Prepare, convert, and activate one package under the caller's resource owner.

        Returns:
            A ready entry and its complete converted overrides, if needed.

        """
        require_running(self.stop_requested)
        selected = environment.ExtensionEnvironment(
            extension_info=source.selection.extension_info, runtime_revision=self.runtime_revision,
        )
        worker = None
        if source.artifact.manifest.backend is not None:
            request = WorkerLoadRequest(
                manifest=source.artifact.manifest, environment=selected,
                peer_schemas=tuple(schema for schema in self.schemas
                                  if schema.reference.owner != source.artifact.manifest.extension_id),
            )
            prepared = self.workers.prepare_worker(request, self.services(request))
            worker = cleanup.enter_context(closing(prepared))
        return self._activate(source, selected, worker)

    def _activate(
        self, source: runtime_plan.PreparationPackage, selected: environment.ExtensionEnvironment,
        worker: worker_contract.ExtensionWorker | None,
    ) -> PreparedPackage:
        require_running(self.stop_requested)
        change = self._migrate(source, worker)
        if isinstance(source.selection, runtime_candidates.MigratingRuntimePackage):
            if change is None:
                message = "candidate settings migration returned no complete values"
                raise RuntimePreparationError(message)
            captured = settings.capture_settings(source.artifact.manifest, change.settings)
        else:
            captured = source.selection.settings
        if worker is not None:
            require_running(self.stop_requested)
            _activate_worker(worker, self.runtime_revision, captured)
        return PreparedPackage(RegistryPackage(
            manifest=source.artifact.manifest, entry=directory.DirectoryEntry(
                extension_info=source.selection.extension_info, state="enabled",
            ), environment=selected, plugin=None if worker is None else worker.plugin, settings=captured,
        ), change)

    def _migrate(
        self, source: runtime_plan.PreparationPackage, worker: worker_contract.ExtensionWorker | None,
    ) -> lifecycle_selection.SettingsChange | None:
        if not isinstance(source.selection, runtime_candidates.MigratingRuntimePackage):
            return None
        if worker is None or worker.plugin.capabilities.migrations is None:
            message = "candidate worker has no declared settings migration capability"
            raise RuntimePreparationError(message)
        converted = runtime_settings_migration.SettingsMigration(
            source.artifact.manifest, worker.plugin.capabilities.migrations, self.runtime_revision,
            source.selection.source, self.stop_requested,
        ).resolve()
        return lifecycle_selection.SettingsChange(
            extension_id=source.selection.extension_info.extension_id,
            package_digest=source.selection.extension_info.package_digest,
            expected_revision=source.selection.source.revision, settings=converted,
        )


def _activate_worker(
    worker: worker_contract.ExtensionWorker, revision: str, captured: registry.RuntimeSettings,
) -> None:
    response = worker.plugin.capabilities.lifecycle.activate(lifecycle.ActivationRequest(
        runtime_revision=revision, settings_revision=captured.revision, settings=captured.default,
    ))
    if response.runtime_revision != revision or response.kind != "ready":
        message = "extension lifecycle did not accept the prepared runtime"
        raise RuntimePreparationError(message)


def require_running(stop_requested: Event | None) -> None:
    """Stop between bounded worker calls without returning partial candidate settings.

    Raises:
        RuntimePreparationStoppedError: If the manager requested a stop.

    """
    if stop_requested is not None and stop_requested.is_set():
        message = "extension runtime preparation was stopped"
        raise RuntimePreparationStoppedError(message)
