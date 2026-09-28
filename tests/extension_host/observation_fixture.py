# Copyright (c) 2026 Zhambyl Yermagambet
"""Register a real external source declaration for observation repository tests."""

from dataclasses import dataclass
from pathlib import Path

from baqylau_extension_api.manifest.package import ExtensionManifest
from baqylau_extension_api.models import lifecycle as public_lifecycle, scopes
from baqylau_extension_api.versions import API_VERSION

from extensions.models import lifecycle_selection, settings as extension_settings
from extensions.models.observations import ExtensionObservation, ObservationAppend, StoredObservation
from repository.impl.sqlite import connection as sqlite_connection, extension_lifecycle as lifecycle_store
from repository.impl.sqlite.observations import SqliteObservationRepository
from tests import storage_reads
from tests.extension_api import source_samples
from tests.extension_host import catalog_fixture, lifecycle_fixture as lifecycle, package_fixture


@dataclass(frozen=True)
class ObservationCase:
    """Keep real storage, a committed runtime, and original input together."""

    lifecycle: lifecycle_store.SqliteExtensionLifecycleRepository
    store: SqliteObservationRepository
    request: ObservationAppend


def installed(directory: Path, manifest: ExtensionManifest | None = None) -> ObservationCase:
    """Commit a checked source package without claiming worker execution.

    Returns:
        A real repository and a request for the exact committed runtime.

    """
    package = _package(directory, source_samples.manifest() if manifest is None else manifest)
    store = lifecycle.claimed_repository(directory)
    proposal = lifecycle.proposal(store, package)
    lifecycle.commit(store, proposal)
    return ObservationCase(store, SqliteObservationRepository(store.database), ObservationAppend(
        extension_id=package.extension_info.extension_id, manager_id=lifecycle.MANAGER,
        runtime_revision=proposal.candidate.runtime_revision,
        scope=source_samples.context().binding.scope, observed_at=lifecycle.NOW,
        observations=source_samples.batch().observations,
    ))


def _package(directory: Path, manifest: ExtensionManifest) -> lifecycle_selection.RuntimePackageSelection:
    source = package_fixture.write_package(directory / "packages", manifest.extension_id)
    manifest = manifest.model_copy(update={"backend": package_fixture.read_manifest(source).backend})
    package_fixture.save_manifest(source, manifest)
    catalog = catalog_fixture.service(directory).rescan_packages(0).snapshot
    entry = catalog.entries[0]
    assert entry.issue is None and entry.package_digest is not None
    return lifecycle_selection.RuntimePackageSelection(
        extension_info=public_lifecycle.ExtensionInfo(
            extension_id=manifest.extension_id, package_version=manifest.package_version,
            api_version=API_VERSION, package_digest=entry.package_digest,
        ), settings=extension_settings.capture_settings(manifest, extension_settings.SettingsOverrides()),
    )


def require_no_fake_session(database: sqlite_connection.SqliteDatabase) -> None:
    """Check stored branch fields and the absence of a made-up session."""
    with database.read() as connection:
        row = connection.execute("SELECT origin, session_id, harness, actor_id FROM raw_events").fetchone()
        sessions = connection.execute("SELECT COUNT(*) FROM sessions").fetchone()
    assert tuple(row) == ("extension", None, None, None)
    assert sessions[0] == 0


def append_in_scope(case: ObservationCase, scope: scopes.ExtensionScope) -> StoredObservation:
    """Store one source input under the selected test scope.

    Returns:
        The newly accepted observation.

    """
    request = select_scope(case.request, scope)
    return storage_reads.append_observations(case.store, request).accepted[0]


def select_scope(request: ObservationAppend, scope: scopes.ExtensionScope) -> ObservationAppend:
    """Select another complete scope without changing the source key.

    Returns:
        A complete scoped append request.

    """
    positioned = request.observations[0]
    return request.model_copy(update={
        "scope": scope, "observations": (positioned.model_copy(update={
            "observation": positioned.observation.model_copy(update={"scope": scope}),
        }),),
    })


def original(stored: StoredObservation) -> ExtensionObservation:
    """Select the extension branch in storage assertions.

    Returns:
        The complete original extension input.

    """
    assert isinstance(stored.observation, ExtensionObservation)
    return stored.observation
