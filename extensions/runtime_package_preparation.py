# Copyright (c) 2026 Zhambyl Yermagambet
"""Convert settings before activating the same privately owned candidate worker."""

from collections.abc import Callable
from contextlib import ExitStack, closing
from dataclasses import dataclass
from threading import Event

from baqylau_extension_api.contracts import migrations as migration_contracts, services as service_contracts
from baqylau_extension_api.models import directory, documents, environment, lifecycle
from baqylau_extension_api.runtime.worker_models import WorkerLoadRequest

from extensions import (
    runtime_plan,
    runtime_record_migration,
    runtime_settings_migration,
    service_removal,
    worker_contract,
)
from extensions.models import (
    lifecycle_selection,
    record_migration,
    registry,
    runtime_candidates,
    settings,
    settings_migration,
)
from extensions.registry_package import RegistryPackage
from extensions.runtime_preparation_contract import RuntimePreparationError, require_running
from repository.contract.record_migrations import RecordMigrationStore


@dataclass(frozen=True)
class PreparedPackage:
    """Keep converted raw overrides and converted records with their activated registry entry."""

    package: RegistryPackage
    settings_change: lifecycle_selection.SettingsChange | None = None
    record_generation: record_migration.RecordGeneration | None = None


@dataclass(frozen=True)
class RuntimePackagePreparation:
    """Share one candidate context without giving feature code storage access."""

    workers: worker_contract.ExtensionWorkers
    services: Callable[[WorkerLoadRequest], service_contracts.ExtensionHostServices]
    runtime_revision: str
    schemas: tuple[documents.SchemaDefinition, ...]
    stop_requested: Event | None
    record_migrations: RecordMigrationStore | None = None
    retired_services: tuple[lifecycle.RemovedService, ...] = ()

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
        require_running(self.stop_requested)
        record_generation = self._migrate_records(source, worker, cleanup)
        return self._activate(source, selected, worker, record_generation)

    def _activate(
        self, source: runtime_plan.PreparationPackage, selected: environment.ExtensionEnvironment,
        worker: worker_contract.ExtensionWorker | None, record_generation: record_migration.RecordGeneration | None,
    ) -> PreparedPackage:
        require_running(self.stop_requested)
        change = self._migrate_settings(source, worker)
        if isinstance(source.selection, runtime_candidates.MigratingRuntimePackage):
            overrides = source.selection.source if change is None else change.settings
            captured = settings.capture_settings(source.artifact.manifest, overrides)
        else:
            captured = source.selection.settings
        if worker is not None:
            require_running(self.stop_requested)
            _activate_worker(
                worker, self.runtime_revision, captured,
                service_removal.removed_for(source.artifact.manifest, self.retired_services),
            )
        return PreparedPackage(RegistryPackage(
            manifest=source.artifact.manifest, entry=directory.DirectoryEntry(
                extension_info=source.selection.extension_info, state="enabled",
            ), environment=selected, plugin=None if worker is None else worker.plugin, settings=captured,
        ), change, record_generation)

    def _migrate_settings(
        self, source: runtime_plan.PreparationPackage, worker: worker_contract.ExtensionWorker | None,
    ) -> lifecycle_selection.SettingsChange | None:
        selection = source.selection
        if not isinstance(selection, runtime_candidates.MigratingRuntimePackage):
            return None
        if not settings_migration.needs_settings_migration(source.artifact.manifest, selection.source):
            return None
        converted = runtime_settings_migration.SettingsMigration(
            source.artifact.manifest, _migrations(worker), self.runtime_revision, selection.source, self.stop_requested,
        ).resolve()
        return lifecycle_selection.SettingsChange(
            extension_id=selection.extension_info.extension_id,
            package_digest=selection.extension_info.package_digest,
            expected_revision=selection.source.revision, settings=converted,
        )

    def _migrate_records(
        self, source: runtime_plan.PreparationPackage, worker: worker_contract.ExtensionWorker | None,
        cleanup: ExitStack,
    ) -> record_migration.RecordGeneration | None:
        """Convert stored records before activation; a later failure or a lost commit discards the copy.

        Returns:
            The migrating generation, or None when the package needs no record conversion.

        Raises:
            RuntimePreparationError: If the host has no record migration store.

        """
        selection = source.selection
        if not isinstance(selection, runtime_candidates.MigratingRuntimePackage) or not selection.records:
            return None
        if self.record_migrations is None:
            message = "the host cannot migrate extension records"
            raise RuntimePreparationError(message)
        record_generation = runtime_record_migration.RecordMigration(
            source.artifact.manifest, _migrations(worker), self.runtime_revision, self.record_migrations,
            self.stop_requested,
        ).migrate(selection.records)
        cleanup.callback(self.record_migrations.fail, record_generation)
        return record_generation


def _migrations(worker: worker_contract.ExtensionWorker | None) -> migration_contracts.ExtensionMigrations:
    if worker is None or worker.plugin.capabilities.migrations is None:
        message = "candidate worker has no declared migration capability"
        raise RuntimePreparationError(message)
    return worker.plugin.capabilities.migrations


def _activate_worker(
    worker: worker_contract.ExtensionWorker,
    revision: str,
    captured: registry.RuntimeSettings,
    removed: tuple[lifecycle.RemovedService, ...],
) -> None:
    response = worker.plugin.capabilities.lifecycle.activate(lifecycle.ActivationRequest(
        runtime_revision=revision, settings_revision=captured.revision, settings=captured.default,
        removed_services=removed,
    ))
    if response.runtime_revision != revision or response.kind != "ready":
        message = "extension lifecycle did not accept the prepared runtime"
        raise RuntimePreparationError(message)
