# Copyright (c) 2026 Zhambyl Yermagambet
"""Use the real manager, native lease, and store with a controlled resource close."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from threading import Event

from extensions import manager, manager_resources as resources, manager_retirement
from extensions.registry_snapshot import RuntimeSnapshot
from extensions.runtime_ownership import FilesystemRuntimeOwnership
from extensions.runtime_preparation_contract import PreparedExtensionRuntime
from repository.impl.sqlite.extension_lifecycle import SqliteExtensionLifecycleRepository
from tests.extension_host import catalog_fixture, lifecycle_fixture, retirement_fixture, runtime_host_fixture


@dataclass
class StoredCloseProbe(PreparedExtensionRuntime):
    """Check that unresolved work is committed before physical closure starts."""

    probe: retirement_fixture.RetirementProbe
    store: SqliteExtensionLifecycleRepository
    resolution: None = None

    @property
    def snapshot(self) -> RuntimeSnapshot:
        """The fixture's runtime before its resources close."""
        return self.probe.snapshot

    def close(self) -> None:
        """Use a fresh repository read at the exact physical-close boundary."""
        record = self.store.read_extension_lifecycle().last_shutdown
        assert record is not None and not record.runtimes[0].resources_closed
        self.probe.close()


def session(directory: Path, probe: retirement_fixture.RetirementProbe) -> resources.ManagerSession:
    """Keep every production ownership boundary except the final worker double.

    Returns:
        A manager session with a drained runtime ready for shutdown.

    """
    host = runtime_host_fixture.host(directory)
    services = resources.ManagerServices(
        host.store, catalog_fixture.repository(directory), host.preparation.registry, host.preparation,
        FilesystemRuntimeOwnership(directory).acquire_runtime(), resources.ManagerCallbacks(Event().set),
        lifecycle_fixture.MANAGER,
    )
    runtime = resources.ManagerRuntime(registry_revision=0, retired=(manager_retirement.retirement_owner(
        StoredCloseProbe(probe, host.store),
    ),))
    return resources.ManagerSession(
        services, runtime, ThreadPoolExecutor(max_workers=1), Event(), resources.ManagerPolicy(),
    )


def controller(session: resources.ManagerSession) -> manager.ManagedExtensions:
    """Construct the production controller without asynchronous startup restoration.

    Returns:
        The normal manager close implementation over the controlled resources.

    """
    return manager.ManagedExtensions(session)
