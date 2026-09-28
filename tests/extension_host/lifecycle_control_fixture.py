# Copyright (c) 2026 Zhambyl Yermagambet
"""Exercise user request planning with real capture, storage, and runtime ownership."""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from extensions import capture_scanner, discovery
from extensions.lifecycle_control import LifecycleControl
from extensions.models.lifecycle_requests import LifecycleRequest
from repository.impl.sqlite.record_migrations import SqliteRecordMigrationStore
from tests.extension_host import catalog_fixture, manager_fixture, package_fixture, runtime_host_fixture


@dataclass(frozen=True)
class ControlHost:
    """Keep the request service separate from the test-driven engine boundary."""

    host: manager_fixture.ManagerHost
    control: LifecycleControl

    def close(self) -> None:
        """Release actual manager resources before temporary test files are removed."""
        self.host.close()

    def request(
        self, action: Literal["enable", "disable", "reload"], request_id: str,
        extension_id: str = package_fixture.OWNER,
    ) -> LifecycleRequest:
        """Select current revisions and exact discovered bytes as a client would.

        Returns:
            A user request with no manager ID or runtime identity.

        """
        catalog = self.control.catalog.read_extension_catalog()
        digest = None
        if action != "disable":
            digest = next(entry.package_digest for entry in catalog.entries
                          if entry.manifest is not None and entry.manifest.extension_id == extension_id)
        return LifecycleRequest(
            action=action, request_id=request_id,
            expected_revision=self.host.controller.read_state().lifecycle.revision,
            expected_catalog_revision=catalog.revision, package_digest=digest,
        )

    def rescan(self) -> None:
        """Capture changed source bytes through the production scanner."""
        catalog = catalog_fixture.service(self.host.runtime.root, capture_scanner.CapturingExtensionScanner(
            discovery.FilesystemExtensionScanner(), self.host.runtime.preparation.artifacts,
        ))
        assert catalog.rescan_packages(catalog.catalog_snapshot().revision).accepted


def open_control(directory: Path) -> ControlHost:
    """Compose a real manager and user planning service without HTTP or feature mocks.

    Returns:
        A running empty runtime with all external metadata captured.

    """
    runtime = runtime_host_fixture.host(directory, claimed=False)
    host = manager_fixture.start_manager(runtime, runtime.preparation)
    catalog = catalog_fixture.repository(directory)
    control = ControlHost(host, LifecycleControl(
        host.controller, catalog, records=SqliteRecordMigrationStore(catalog.database),
    ))
    host.finish()
    control.rescan()
    return control
