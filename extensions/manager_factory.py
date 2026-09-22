# Copyright (c) 2026 Zhambyl Yermagambet
"""Acquire process ownership before claiming or restoring a stored runtime generation."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, closing
from dataclasses import dataclass, field
from threading import Event
from uuid import uuid4

from extensions import manager, manager_resources as resources, registry_contract
from extensions.manager_contract import ExtensionManager, ManagerStateError
from extensions.models import lifecycle_operations, lifecycle_selection, lifecycle_state
from extensions.runtime_ownership_contract import ExtensionRuntimeOwnership
from extensions.runtime_preparation_contract import ExtensionRuntimePreparation
from repository.contract import extension_catalog, extension_lifecycle


@dataclass(frozen=True)
class ExtensionManagerFactory:
    """Start one serial preparation executor and transfer its lease to the manager."""

    repository: extension_lifecycle.ExtensionLifecycleRepository
    catalog: extension_catalog.ExtensionCatalogRepository
    registry: registry_contract.ExtensionRegistry
    preparation: ExtensionRuntimePreparation
    ownership: ExtensionRuntimeOwnership
    callbacks: resources.ManagerCallbacks
    policy: resources.ManagerPolicy = field(default_factory=resources.ManagerPolicy)

    def open_manager(self) -> ExtensionManager:
        """Start restoration without running feature preparation on the caller thread.

        Returns:
            An owned manager with a durable pending restore operation.

        """
        with ExitStack() as cleanup:
            lease = cleanup.enter_context(closing(self.ownership.acquire_runtime()))
            services = resources.ManagerServices(
                self.repository, self.catalog, self.registry, self.preparation, lease, self.callbacks,
                f"manager-{uuid4().hex}",
            )
            session = _open_session(services, self.policy, cleanup)
            controller = manager.ManagedExtensions(session)
            _restore(controller, services)
            cleanup.pop_all()
            return controller


def _open_session(
    services: resources.ManagerServices, policy: resources.ManagerPolicy, cleanup: ExitStack,
) -> resources.ManagerSession:
    with services.registry.read_snapshot() as selected:
        if selected.snapshot.active_order:
            message = "a new manager requires an unowned empty registry"
            raise ManagerStateError(message)
        revision = selected.revision
    _claim(services)
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="baqylau-extension-lifecycle")
    cleanup.callback(executor.shutdown, wait=True, cancel_futures=False)
    stopped = Event()
    cleanup.callback(stopped.set)
    return resources.ManagerSession(services, resources.ManagerRuntime(revision), executor, stopped, policy)


def _claim(services: resources.ManagerServices) -> None:
    with services.lease.hold_ownership():
        state = services.repository.read_extension_lifecycle()
        claimed = services.repository.claim_extension_manager(lifecycle_state.ManagerClaim(
            expected_revision=state.revision, manager_id=services.manager_id, claimed_at=services.callbacks.clock(),
        ))
    if not claimed.accepted:
        message = "extension manager ownership claim was stale"
        raise ManagerStateError(message)


def _restore(controller: ExtensionManager, services: resources.ManagerServices) -> None:
    state = services.repository.read_extension_lifecycle()
    catalog = services.catalog.read_extension_catalog()
    selected = state.committed_runtime
    proposal = lifecycle_operations.LifecycleProposal(
        operation_id=f"restore-{uuid4().hex}", manager_id=services.manager_id, expected_revision=state.revision,
        kind="restore", candidate=lifecycle_selection.RuntimeSelection(
            runtime_revision=f"runtime-{uuid4().hex}", catalog_revision=catalog.revision,
            packages=() if selected is None else selected.packages,
        ),
    )
    if controller.submit_operation(proposal).status != "accepted":
        message = "extension runtime restore conflicted with another state change"
        raise ManagerStateError(message)
