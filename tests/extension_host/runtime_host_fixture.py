# Copyright (c) 2026 Zhambyl Yermagambet
"""Join fixed package capture, stored operations, and real private runtime workers."""

from dataclasses import dataclass
from pathlib import Path

from baqylau_extension_api.manifest.activation import activation_order
from baqylau_extension_api.models.lifecycle import ExtensionInfo
from baqylau_extension_api.runtime.call_grants import HostCallLedger
from baqylau_extension_api.versions import API_VERSION

from extensions import (
    capture_scanner,
    discovery,
    environments,
    preparation_runner,
    preparation_services,
    registry,
    runtime_preparation,
)
from extensions.impl.process.factory import ProcessExtensionWorkers
from extensions.models import catalog as catalogs, lifecycle_operations as operations, lifecycle_selection as selection
from extensions.models.settings import SettingsOverrides, capture_settings
from repository.impl.sqlite import extension_lifecycle, record_migrations
from tests.extension_host import artifact_fixture, catalog_fixture, lifecycle_fixture, registry_process_fixture


@dataclass(frozen=True)
class RuntimeHost:
    """Keep production services separate from the test's manual publication steps."""

    root: Path
    preparation: runtime_preparation.RuntimePreparation
    store: extension_lifecycle.SqliteExtensionLifecycleRepository

    def accept(self, operation_id: str = "enable") -> operations.LifecycleOperation:
        """Capture discovered packages and accept their complete checked selection.

        Returns:
            A durable pending operation; no worker starts from this method.

        """
        catalog = catalog_fixture.service(self.root, capture_scanner.CapturingExtensionScanner(
            discovery.FilesystemExtensionScanner(), self.preparation.artifacts,
        ))
        snapshot = catalog.rescan_packages(catalog.catalog_snapshot().revision).snapshot
        proposed = lifecycle_fixture.proposal(self.store, operation_id=operation_id).model_copy(update={
            "kind": "enable", "candidate": candidate(snapshot, operation_id),
        })
        admitted = self.store.accept_extension_operation(proposed, lifecycle_fixture.NOW)
        assert admitted.status == "accepted" and admitted.operation is not None
        return admitted.operation


def host(directory: Path, *, claimed: bool = True) -> RuntimeHost:
    """Compose actual runtime services without starting a daemon or touching user data.

    Returns:
        Production contracts and a private lifecycle repository.

    """
    artifacts = artifact_fixture.store(directory)
    ledger = HostCallLedger()
    private_environments = environments.LocalExtensionEnvironments(
        directory / "environments", artifacts, preparation_runner.BoundedPreparationRunner(),
    )
    prepared = runtime_preparation.RuntimePreparation(
        artifacts, ProcessExtensionWorkers(private_environments, ledger),
        registry.ActiveExtensionRegistry("initial"), ledger,
        preparation_services.PreparationServices(
            record_migrations=record_migrations.SqliteRecordMigrationStore(
                catalog_fixture.repository(directory).database,
            ),
            process_runner=preparation_runner.BoundedPreparationRunner(),
        ),
    )
    store = lifecycle_fixture.claimed_repository(directory) if claimed else lifecycle_fixture.repository(directory)
    return RuntimeHost(directory, prepared, store)


def write_peers(
    directory: Path, wheels: Path, owners: tuple[str, ...],
) -> tuple[Path, ...]:
    """Keep each feature's source and declared E2E file in its own external package.

    Returns:
        Source directories which can change after the host captures their bytes.

    """
    root = directory / "packages"
    root.mkdir(exist_ok=True)
    return tuple(_write_peer(directory, wheels, owner) for owner in owners)


def _write_peer(directory: Path, wheels: Path, owner: str) -> Path:
    source = registry_process_fixture.write_peer(directory / "build" / owner, wheels, owner)
    target = directory / "packages" / owner
    source.rename(target)
    return target


def candidate(snapshot: catalogs.ExtensionCatalogSnapshot, operation_id: str) -> selection.RuntimeSelection:
    """Select the captured fixture packages in checked dependency order.

    Returns:
        A complete candidate without accepting it in storage.

    """
    assert not snapshot.root_issues
    assert all(entry.issue is None for entry in snapshot.entries)
    packages = tuple(selection.RuntimePackageSelection(
        extension_info=ExtensionInfo(
            extension_id=entry.manifest.extension_id, package_version=entry.manifest.package_version,
            api_version=API_VERSION, package_digest=entry.package_digest,
        ), settings=capture_settings(entry.manifest, SettingsOverrides()),
    ) for entry in snapshot.entries if entry.manifest is not None and entry.package_digest is not None)
    order = activation_order(tuple(
        entry.manifest for entry in snapshot.entries if entry.manifest is not None
    ))
    return selection.RuntimeSelection(
        runtime_revision=f"runtime-{operation_id}", catalog_revision=snapshot.revision,
        packages=tuple(package for owner in order for package in packages
                       if package.extension_info.extension_id == owner),
    )
