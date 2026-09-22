# Copyright (c) 2026 Zhambyl Yermagambet
"""Prepare actual stored migration inputs without claiming worker execution."""

from dataclasses import dataclass
from pathlib import Path

from baqylau_extension_api.models.lifecycle import ExtensionInfo
from baqylau_extension_api.models.scopes import WorkspaceScope
from baqylau_extension_api.versions import API_VERSION

from extensions.models import lifecycle_operations as operations, lifecycle_selection as selection, registry
from extensions.models.runtime_candidates import MigratingRuntimePackage, MigratingRuntimeSelection
from extensions.models.runtime_resolution import RuntimeResolution
from extensions.models.settings import SettingsOverrides, capture_settings
from repository.impl.sqlite.extension_lifecycle import SqliteExtensionLifecycleRepository
from tests.extension_api import migration_samples as samples
from tests.extension_host import catalog_fixture, lifecycle_fixture, lifecycle_settings_fixture, package_fixture

WORKSPACE = WorkspaceScope(workspace_id="retained-workspace")


@dataclass(frozen=True)
class MigrationStore:
    """Keep a complete selected source and independent expected conversion."""

    store: SqliteExtensionLifecycleRepository
    proposal: operations.LifecycleProposal
    resolution: RuntimeResolution

    def admit(self) -> operations.LifecycleOperation:
        """Reserve the actual candidate without simulating its completion.

        Returns:
            The durable accepted operation.

        """
        admitted = self.store.accept_extension_operation(self.proposal, lifecycle_fixture.NOW)
        assert admitted.status == "accepted" and admitted.operation is not None
        return admitted.operation


def migration_store(directory: Path) -> MigrationStore:
    """Seed old values, install new metadata, and select one unresolved candidate.

    Returns:
        A real private repository with no admitted migration yet.

    """
    store = lifecycle_fixture.claimed_repository(directory)
    source = package_fixture.write_package(directory / "packages", samples.OWNER)
    original = _install(directory, source, 1)
    lifecycle_fixture.commit(store, lifecycle_settings_fixture.settings_proposal(store, original, overrides(1)))
    target = _install(directory, source, 2)
    proposed = lifecycle_fixture.proposal(store, target, "convert")
    return MigrationStore(store, proposed.model_copy(update={"candidate": MigratingRuntimeSelection(
        runtime_revision=proposed.candidate.runtime_revision, catalog_revision=proposed.candidate.catalog_revision,
        packages=(MigratingRuntimePackage(extension_info=target.extension_info, source=overrides(1)),),
    )}), RuntimeResolution(
        runtime=selection.RuntimeSelection(
            runtime_revision=proposed.candidate.runtime_revision, catalog_revision=proposed.candidate.catalog_revision,
            packages=(target.model_copy(update={
                "settings": capture_settings(samples.manifest(), overrides(2)),
            }),),
        ), settings_changes=(selection.SettingsChange(
            extension_id=samples.OWNER, package_digest=target.extension_info.package_digest,
            expected_revision=1, settings=overrides(2),
        ),),
    ))


def overrides(version: int) -> SettingsOverrides:
    """Use explicit installation and workspace documents in one exact schema.

    Returns:
        Raw choices with a separately advanced owner revision.

    """
    encoded = '{"label":"Chosen"}' if version == 1 else '{"title":"Chosen"}'
    return SettingsOverrides(revision=version, installation=samples.document(version, encoded), scopes=(
        registry.ScopedRuntimeSettings(scope=WORKSPACE, settings=samples.document(version, encoded)),
    ))


def _install(directory: Path, source: Path, version: int) -> selection.RuntimePackageSelection:
    previous = package_fixture.read_manifest(source)
    manifest = samples.manifest(downgrade=version == 1)
    assert manifest.settings is not None
    manifest = manifest.model_copy(update={
        "package_version": f"{version}.0.0", "backend": previous.backend,
        "settings": manifest.settings.model_copy(update={
            "scopes": ("installation", "workspace"),
        }),
    })
    package_fixture.save_manifest(source, manifest)
    catalog = catalog_fixture.service(directory)
    snapshot = catalog.rescan_packages(catalog.catalog_snapshot().revision).snapshot
    assert len(snapshot.entries) == 1
    assert snapshot.entries[0].issue is None
    digest = snapshot.entries[0].package_digest
    assert digest is not None
    return selection.RuntimePackageSelection(extension_info=ExtensionInfo(
        extension_id=samples.OWNER, package_version=manifest.package_version, api_version=API_VERSION,
        package_digest=digest,
    ), settings=capture_settings(manifest, SettingsOverrides()))
