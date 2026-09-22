# Copyright (c) 2026 Zhambyl Yermagambet
"""Use real SQLite files and checked external declarations for lifecycle tests."""

from pathlib import Path

from baqylau_extension_api.manifest import package as package_models, settings
from baqylau_extension_api.models.documents import EncodedDocument
from baqylau_extension_api.models.lifecycle import ExtensionInfo
from baqylau_extension_api.versions import API_VERSION

from extensions.models import lifecycle_operations as operations, lifecycle_selection as selection
from extensions.models.lifecycle_state import ManagerClaim
from extensions.models.settings import SettingsOverrides, capture_settings
from repository.impl.sqlite.extension_lifecycle import SqliteExtensionLifecycleRepository
from tests.extension_api import service_samples
from tests.extension_host import catalog_fixture, package_fixture

MANAGER = "manager-one"
OPERATION = "operation-one"
NOW = 1000.0


def repository(directory: Path) -> SqliteExtensionLifecycleRepository:
    """Use a fresh database object for one private test file.

    Returns:
        The production lifecycle repository.

    """
    return SqliteExtensionLifecycleRepository(catalog_fixture.repository(directory).database)


def claimed_repository(directory: Path) -> SqliteExtensionLifecycleRepository:
    """Claim the initial manager generation before accepting work.

    Returns:
        A real store with one accepted manager claim.

    """
    store = repository(directory)
    assert store.claim_extension_manager(ManagerClaim(expected_revision=0, manager_id=MANAGER, claimed_at=NOW)).accepted
    return store


def install_package(directory: Path, owner: str = package_fixture.OWNER) -> selection.RuntimePackageSelection:
    """Retain a package declaration with explicit installation and workspace settings.

    Returns:
        Its checked identity and initial captured defaults.

    """
    source = package_fixture.write_package(directory / "packages", owner)
    manifest = _write_manifest(source, owner)
    catalog = catalog_fixture.service(directory)
    snapshot = catalog.rescan_packages(catalog.catalog_snapshot().revision).snapshot
    captured = next(entry for entry in snapshot.entries if entry.manifest == manifest)
    assert captured.package_digest is not None and captured.issue is None
    return selection.RuntimePackageSelection(
        extension_info=ExtensionInfo(
            extension_id=owner, package_version=manifest.package_version,
            api_version=API_VERSION, package_digest=captured.package_digest,
        ), settings=capture_settings(manifest, SettingsOverrides()),
    )


def proposal(
    store: SqliteExtensionLifecycleRepository, package: selection.RuntimePackageSelection | None = None,
    operation_id: str = OPERATION,
) -> operations.LifecycleProposal:
    """Select current revisions and one complete candidate for a test operation.

    Returns:
        A valid enable or empty restore proposal before explicit fault injection.

    """
    state = store.read_extension_lifecycle()
    catalog = catalog_fixture.repository(Path(store.database.path).parent).read_extension_catalog()
    assert state.manager_id is not None
    return operations.LifecycleProposal(
        operation_id=operation_id, manager_id=state.manager_id, expected_revision=state.revision,
        kind="restore" if package is None else "enable", candidate=selection.RuntimeSelection(
            runtime_revision=f"runtime-{operation_id}", catalog_revision=catalog.revision,
            packages=() if package is None else (package,),
        ), intents=() if package is None else (selection.ExtensionIntent(
            extension_id=package.extension_info.extension_id, enabled=True,
            package_digest=package.extension_info.package_digest,
        ),),
    )


def completion(operation: operations.LifecycleOperation) -> operations.LifecycleCompletion:
    """Select the exact accepted request before reporting successful preparation.

    Returns:
        A completion with no replacement candidate or feature-selected revision.

    """
    return operations.LifecycleCompletion(
        manager_id=operation.proposal.manager_id, operation_id=operation.proposal.operation_id,
        expected_revision=operation.accepted_revision, completed_at=NOW + 1,
    )


def commit(store: SqliteExtensionLifecycleRepository, proposed: operations.LifecycleProposal) -> None:
    """Accept and finish a fixture operation without running feature code."""
    admitted = store.accept_extension_operation(proposed, NOW)
    assert admitted.status == "accepted" and admitted.operation is not None
    assert store.finish_extension_operation(completion(admitted.operation)).accepted


def _write_manifest(source: Path, owner: str) -> package_models.ExtensionManifest:
    original = package_fixture.read_manifest(source)
    definition = service_samples.schema(owner)
    manifest = original.model_copy(update={
        "schemas": (definition,), "settings": settings.SettingsDefinition(
            defaults=EncodedDocument(schema_ref=definition.reference, json_text='"default"'),
            scopes=("installation", "workspace"),
        ),
    })
    package_fixture.save_manifest(source, manifest)
    return manifest
