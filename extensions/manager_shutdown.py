# Copyright (c) 2026 Zhambyl Yermagambet
"""Stop admission before releasing runtime resources or the native process lease."""

import logging
from time import monotonic
from uuid import uuid4

from baqylau_extension_api.runtime.host_context import host_call_scope

from extensions import manager_retirement, manager_state
from extensions.manager_contract import ManagerCleanupError
from extensions.manager_resources import ManagerSession
from extensions.models.cleanup import RetirementIssue, ShutdownRecord
from extensions.models.lifecycle_operations import LifecycleFailure

LOGGER = logging.getLogger(__name__)


def drain_registry(session: ManagerSession) -> None:
    """Wait without holding the manager mutex; existing borrowed reads may finish.

    Raises:
        ManagerCleanupError: If admitted calls still own the active workers.

    """
    if not session.services.registry.close_registry(session.policy.drain_seconds):
        message = "extension registry still has admitted calls; runtime ownership remains held"
        raise ManagerCleanupError(message)
    session.executor.shutdown(wait=True, cancel_futures=False)


def collect_owned_work(session: ManagerSession) -> None:
    """Move completed work to retirement after the executor and registry have drained."""
    manager_state.collect_cleanup(session.runtime)
    pending = session.runtime.pending
    if pending is not None:
        outcome = pending.task.result()
        retired = () if outcome.prepared is None else (manager_retirement.retirement_owner(outcome.prepared),)
        manager_state.finish_failed(session.services, session.runtime, LifecycleFailure(
            code="interrupted", detail="The manager stopped before runtime publication.",
        ))
        session.runtime.retired += retired
    if session.runtime.active is not None:
        session.runtime.retired += (manager_retirement.retirement_owner(session.runtime.active),)
        session.runtime.active = None


def close_resources(session: ManagerSession) -> tuple[RetirementIssue, ...]:
    """Run deactivation and resource cleanup without holding the manager mutex.

    Returns:
        Unresolved work which must be recorded while holding the manager mutex.

    """
    with host_call_scope(None, expires_at=monotonic() + session.policy.deactivation_seconds):
        for owner in session.runtime.retired:
            owner.deactivate("shutdown")
    _record(session)
    for owner in session.runtime.retired:
        owner.close_resources()
    record = _record(session)
    issues = tuple(issue for runtime in record.runtimes for issue in runtime.issues)
    if issues:
        LOGGER.warning("Extension shutdown retained %s unresolved issues in record %s", len(issues), record.record_id)
    return issues


def require_closed(session: ManagerSession) -> None:
    """Keep the native lease while physical resources can still execute work.

    Raises:
        ManagerCleanupError: If a retired runtime has no successful resource close.

    """
    if any(not owner.observation().resources_closed for owner in session.runtime.retired):
        message = "extension workers still need cleanup; runtime ownership remains held"
        raise ManagerCleanupError(message)


def _record(session: ManagerSession) -> ShutdownRecord:
    record = ShutdownRecord(
        record_id=f"shutdown-{uuid4().hex}", manager_id=session.services.manager_id,
        recorded_at=session.services.callbacks.clock(),
        runtimes=tuple(owner.observation() for owner in session.runtime.retired),
    )
    try:
        with session.services.lease.hold_ownership():
            accepted = session.services.repository.record_extension_shutdown(record)
    except Exception as exc:
        message = "extension shutdown evidence could not be stored; runtime ownership remains held"
        raise ManagerCleanupError(message) from exc
    if not accepted:
        message = "extension shutdown evidence has an unknown manager; runtime ownership remains held"
        raise ManagerCleanupError(message)
    return record
