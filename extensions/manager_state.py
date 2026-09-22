# Copyright (c) 2026 Zhambyl Yermagambet
"""Check the live manager state separately from stored lifecycle rows."""

from baqylau_extension_api.models.directory import DirectorySnapshot

from extensions.manager_contract import ManagerStateError
from extensions.manager_resources import ManagerRuntime, ManagerServices
from extensions.models import lifecycle_operations as operations, manager


def require_open(runtime: ManagerRuntime) -> None:
    """Reject new state changes after shutdown or manager-generation loss.

    Raises:
        ManagerStateError: If the manager cannot accept another operation.

    """
    if runtime.mode != "running":
        message = "extension manager is not open for state changes"
        raise ManagerStateError(message)


def collect_cleanup(runtime: ManagerRuntime) -> None:
    """Read only a completed cleanup task; never wait for a feature under the mutex."""
    if runtime.cleanup_task is not None and runtime.cleanup_task.done():
        runtime.cleanup_issues = runtime.cleanup_task.result()
        if not runtime.cleanup_issues:
            runtime.retired = ()
        runtime.cleanup_task = None


def manager_snapshot(services: ManagerServices, runtime: ManagerRuntime) -> manager.ManagerSnapshot:
    """Read one mutex-protected manager observation and the current stored state.

    Returns:
        Actual runtime identity, not a stored claim that workers are running.

    """
    phase = runtime.mode
    if phase == "running" and runtime.pending is not None:
        return manager.ManagerSnapshot(
            lifecycle=services.repository.read_extension_lifecycle(), registry_revision=runtime.registry_revision,
            active_runtime=_active_revision(runtime), directory=_active_directory(runtime),
            cleanup=runtime.cleanup_issues,
            cleanup_pending=bool(runtime.retired),
            phase="awaiting_boundary" if runtime.pending.task.done() else "preparing",
        )
    return manager.ManagerSnapshot(
        lifecycle=services.repository.read_extension_lifecycle(), registry_revision=runtime.registry_revision,
        active_runtime=_active_revision(runtime), directory=_active_directory(runtime), phase=phase,
        cleanup=runtime.cleanup_issues,
        cleanup_pending=bool(runtime.retired),
    )


def finish_failed(services: ManagerServices, runtime: ManagerRuntime, failure: operations.LifecycleFailure) -> None:
    """Finish the exact pending operation and reject a lost manager generation."""
    pending = runtime.pending
    if pending is None:
        return
    with services.lease.hold_ownership():
        outcome = services.repository.finish_extension_operation(operations.LifecycleCompletion(
            manager_id=services.manager_id, operation_id=pending.operation.proposal.operation_id,
            expected_revision=pending.operation.accepted_revision, completed_at=services.callbacks.clock(),
            failure=failure,
        ))
    if not outcome.accepted:
        runtime.mode = "fenced"
    runtime.pending = None


def _active_revision(runtime: ManagerRuntime) -> str | None:
    return None if runtime.active is None else runtime.active.snapshot.directory.runtime_revision


def _active_directory(runtime: ManagerRuntime) -> DirectorySnapshot | None:
    return None if runtime.active is None else runtime.active.snapshot.directory
