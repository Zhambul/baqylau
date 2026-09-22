# Copyright (c) 2026 Zhambyl Yermagambet
"""Own one runtime generation without running worker calls under the manager mutex."""

from threading import Lock

from baqylau_extension_api.models.base import Identifier

from extensions import manager_admission, manager_publication, manager_shutdown, manager_state
from extensions.manager_contract import ExtensionManager, ExtensionRuntimeBoundary
from extensions.manager_resources import ManagerSession
from extensions.models import lifecycle_operations, lifecycle_state, manager


class ManagedExtensions(ExtensionManager, ExtensionRuntimeBoundary):
    """Keep request admission, engine publication, and resource cleanup distinct."""

    def __init__(self, session: ManagerSession) -> None:
        """Receive an exclusively claimed manager run from the factory."""
        self._session = session
        self._lock = Lock()
        self._close_lock = Lock()

    def read_state(self) -> manager.ManagerSnapshot:
        """Read progress without waiting for a worker or preparation task.

        Returns:
            The exact stored state and current manager observations.

        """
        with self._lock:
            manager_state.collect_cleanup(self._session.runtime)
            return manager_state.manager_snapshot(self._session.services, self._session.runtime)

    def read_operation(self, operation_id: Identifier) -> lifecycle_operations.LifecycleOperation | None:
        """Read the retained outcome even after this manager stops.

        Returns:
            The requested operation, if it exists.

        """
        return self._session.services.repository.read_extension_operation(operation_id)

    def submit_operation(self, proposal: lifecycle_operations.LifecycleProposal) -> lifecycle_state.LifecycleAdmission:
        """Accept complete host-built requests, not feature-selected runtime authority.

        Returns:
            Stored admission; an accepted request is not yet an enabled runtime.

        """
        with self._lock:
            return manager_admission.submit_operation(self._session, proposal)

    def publish_ready(self) -> manager.ManagerProgress:
        """Try one short switch at the engine's processing boundary.

        Returns:
            Progress without waiting for preparation or calling feature cleanup.

        """
        with self._lock:
            return manager_publication.publish_ready(self._session)

    def retry_cleanup(self) -> None:
        """Retry retained cleanup only; do not replay commands or candidate preparation."""
        with self._lock:
            manager_state.require_open(self._session.runtime)
            manager_state.collect_cleanup(self._session.runtime)
            manager_publication.schedule_cleanup(self._session)

    def close(self) -> None:
        """Drain reads, retain job evidence, and close resources before releasing the lease."""
        with self._close_lock:
            with self._lock:
                if self._session.runtime.mode == "closed":
                    return
                self._session.runtime.mode = "closing"
                self._session.stopped.set()
            manager_shutdown.drain_registry(self._session)
            with self._lock:
                manager_shutdown.collect_owned_work(self._session)
            issues = manager_shutdown.close_resources(self._session)
            with self._lock:
                self._session.runtime.cleanup_issues = issues
                manager_shutdown.require_closed(self._session)
                self._session.services.lease.close()
                self._session.runtime.retired = ()
                self._session.runtime.mode = "closed"
