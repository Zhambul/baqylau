# Copyright (c) 2026 Zhambyl Yermagambet
"""Exercise the manager with real repositories and private runtime resources."""

from dataclasses import dataclass
from pathlib import Path
from threading import Event
from time import monotonic

from extensions import capture_scanner, discovery, manager_contract, manager_factory, manager_resources
from extensions.models import lifecycle_operations as operations, lifecycle_selection as selections
from extensions.runtime_ownership import FilesystemRuntimeOwnership
from extensions.runtime_preparation_contract import ExtensionRuntimePreparation
from tests.extension_host import catalog_fixture, lifecycle_fixture, runtime_host_fixture

WAIT_SECONDS = 30


@dataclass(frozen=True)
class ManagerHost:
    """Use the production controller; only the test drives the engine boundary."""

    runtime: runtime_host_fixture.RuntimeHost
    controller: manager_contract.ExtensionManager
    notice: Event

    def close(self) -> None:
        """Stop the manager before a test removes its private files."""
        self.controller.close()

    def proposal(self, operation_id: str, owners: tuple[str, ...] | None = None) -> operations.LifecycleProposal:
        """Build a complete host request without admitting it in the repository.

        Returns:
            The exact candidate which the test submits through the manager.

        """
        catalog = catalog_fixture.service(self.runtime.root, capture_scanner.CapturingExtensionScanner(
            discovery.FilesystemExtensionScanner(), self.runtime.preparation.artifacts,
        ))
        snapshot = catalog.rescan_packages(catalog.catalog_snapshot().revision).snapshot
        candidate = runtime_host_fixture.candidate(snapshot, operation_id)
        if owners is not None:
            candidate = candidate.model_copy(update={"packages": tuple(
                package for package in candidate.packages if package.extension_info.extension_id in owners
            )})
        return lifecycle_fixture.proposal(self.runtime.store, operation_id=operation_id).model_copy(update={
            "candidate": candidate, "kind": "enable" if candidate.packages else "disable",
            "intents": _intents(candidate, self.runtime.store.read_extension_lifecycle().committed_runtime),
        })

    def finish(self, expected: str = "published") -> None:
        """Wait on actual task notices and perform publication on this test thread."""
        deadline = monotonic() + WAIT_SECONDS
        while True:
            progress = self.controller.publish_ready()
            if progress.status != "preparing":
                assert progress.status == expected
                self.wait_cleanup()
                return
            assert self.notice.wait(max(0, deadline - monotonic())), "manager preparation did not finish"
            self.notice.clear()

    def wait_cleanup(self) -> None:
        """Wait on task notices, stopping when cleanup succeeds or reports uncertainty."""
        deadline = monotonic() + WAIT_SECONDS
        while self.controller.read_state().cleanup_pending:
            if self.controller.read_state().cleanup:
                return
            assert self.notice.wait(max(0, deadline - monotonic())), "manager cleanup did not finish"
            self.notice.clear()

    def wait_ready(self) -> None:
        """Wait for preparation without publishing its completed candidate."""
        deadline = monotonic() + WAIT_SECONDS
        while self.controller.read_state().phase == "preparing":
            assert self.notice.wait(max(0, deadline - monotonic())), "manager preparation did not finish"
            self.notice.clear()
        assert self.controller.read_state().phase == "awaiting_boundary"


def open_manager(directory: Path, *, drain_seconds: float = 1) -> ManagerHost:
    """Acquire the real process lock before the manager claims its stored generation.

    Returns:
        An owned manager with asynchronous startup restoration in progress.

    """
    runtime = runtime_host_fixture.host(directory, claimed=False)
    return start_manager(runtime, runtime.preparation, drain_seconds=drain_seconds)


def start_manager(
    runtime: runtime_host_fixture.RuntimeHost, preparation: ExtensionRuntimePreparation, *, drain_seconds: float = 1,
) -> ManagerHost:
    """Allow a controlled preparation boundary while retaining the real manager.

    Returns:
        The owned manager and its actual readiness notice.

    """
    notice = Event()
    factory = manager_factory.ExtensionManagerFactory(
        runtime.store, catalog_fixture.repository(runtime.root), runtime.preparation.registry, preparation,
        FilesystemRuntimeOwnership(runtime.root), manager_resources.ManagerCallbacks(notice.set),
        manager_resources.ManagerPolicy(drain_seconds=drain_seconds),
    )
    return ManagerHost(runtime, factory.open_manager(), notice)


def _intents(
    candidate: selections.RuntimeSelection, previous: selections.RuntimeSelection | None,
) -> tuple[selections.ExtensionIntent, ...]:
    enabled = tuple(selections.ExtensionIntent(
        extension_id=package.extension_info.extension_id, enabled=True,
        package_digest=package.extension_info.package_digest,
    ) for package in candidate.packages)
    old = () if previous is None else previous.packages
    disabled = tuple(selections.ExtensionIntent(
        extension_id=package.extension_info.extension_id, enabled=False,
    ) for package in old if package.extension_info.extension_id not in {entry.extension_id for entry in enabled})
    return (*enabled, *disabled)
