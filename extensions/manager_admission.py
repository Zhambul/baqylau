# Copyright (c) 2026 Zhambyl Yermagambet
"""Accept immutable host requests before scheduling any worker preparation."""

from concurrent.futures import Future

from extensions import manager_preparation, manager_state
from extensions.manager_contract import ManagerStateError
from extensions.manager_resources import ManagerCallbacks, ManagerSession, PendingPreparation, PreparationOutcome
from extensions.models.catalog import ExtensionCatalogSnapshot
from extensions.models.lifecycle_operations import LifecycleFailure, LifecycleOperation, LifecycleProposal
from extensions.models.lifecycle_state import LifecycleAdmission


def submit_operation(session: ManagerSession, proposal: LifecycleProposal) -> LifecycleAdmission:
    """Use the manager mutex and lease for acceptance, not for worker execution.

    Returns:
        Durable admission with at most one newly scheduled preparation.

    Raises:
        ManagerStateError: If cleanup is pending or a request uses another manager ID.

    """
    manager_state.require_open(session.runtime)
    manager_state.collect_cleanup(session.runtime)
    if proposal.manager_id != session.services.manager_id:
        message = "extension request belongs to a different manager generation"
        raise ManagerStateError(message)
    with session.services.lease.hold_ownership():
        if session.services.repository.read_extension_operation(proposal.operation_id) is not None:
            return session.services.repository.accept_extension_operation(proposal, session.services.callbacks.clock())
        if session.runtime.pending is not None or session.runtime.retired:
            return LifecycleAdmission(status="busy", state=session.services.repository.read_extension_lifecycle())
        catalog = session.services.catalog.read_extension_catalog()
        if catalog.revision != proposal.candidate.catalog_revision:
            return _stale(session)
        admitted = session.services.repository.accept_extension_operation(proposal, session.services.callbacks.clock())
    if admitted.status == "accepted" and admitted.operation is not None:
        task = _start_preparation(session, admitted.operation, catalog)
        session.runtime.pending = PendingPreparation(
            admitted.operation, session.runtime.registry_revision, task,
        )
        task.add_done_callback(ReadyNotice(session.services.callbacks).notify)
    return admitted


def _stale(session: ManagerSession) -> LifecycleAdmission:
    return LifecycleAdmission(status="stale", state=session.services.repository.read_extension_lifecycle())


def _start_preparation(
    session: ManagerSession, operation: LifecycleOperation, catalog: ExtensionCatalogSnapshot,
) -> Future[PreparationOutcome]:
    try:
        return session.executor.submit(
            manager_preparation.prepare_operation, session.services, operation, catalog, session.stopped,
        )
    except RuntimeError:
        failed: Future[PreparationOutcome] = Future()
        failed.set_result(PreparationOutcome(failure=LifecycleFailure(
            code="preparation_failed", detail="The runtime preparation executor is unavailable.",
        )))
        return failed


class ReadyNotice:
    """Wake the engine without calling it from the preparation thread."""

    def __init__(self, callbacks: ManagerCallbacks) -> None:
        """Keep a narrow host notification callback."""
        self._callbacks = callbacks

    def notify[Ready](self, completed: Future[Ready]) -> None:
        """Only report readiness; publication remains the engine's responsibility."""
        if completed.done():
            self._callbacks.changed()
