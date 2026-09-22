# Copyright (c) 2026 Zhambyl Yermagambet
"""Publish at the engine boundary and schedule old-resource cleanup elsewhere."""

from concurrent.futures import Future
from typing import Final

from extensions import manager_retirement, manager_state
from extensions.manager_admission import ReadyNotice
from extensions.manager_resources import ManagerSession, PendingPreparation, PreparationOutcome
from extensions.models import cleanup, lifecycle_operations, manager
from extensions.runtime_commit import StoredRegistryCommit

FENCED: Final = "fenced"


def publish_ready(session: ManagerSession) -> manager.ManagerProgress:
    """Never install dependencies, call a feature, or wait for preparation here.

    Returns:
        The outcome of one short registry publication attempt.

    """
    if session.runtime.mode != "running":
        return _progress(session, FENCED if session.runtime.mode == FENCED else "closing")
    manager_state.collect_cleanup(session.runtime)
    pending = session.runtime.pending
    if pending is None:
        return _progress(session, "idle")
    if not pending.task.done():
        return _progress(session, "preparing")
    outcome = pending.task.result()
    if outcome.failure is not None:
        manager_state.finish_failed(session.services, session.runtime, outcome.failure)
        return _progress(session, "failed" if session.runtime.mode == "running" else FENCED)
    return _publish_candidate(session, pending, outcome)


def schedule_cleanup(session: ManagerSession) -> None:
    """Use the serial background executor, never the engine or request thread."""
    if session.runtime.retired and session.runtime.cleanup_task is None:
        session.runtime.cleanup_task = _start_cleanup(session)
        session.runtime.cleanup_task.add_done_callback(ReadyNotice(session.services.callbacks).notify)


def _start_cleanup(session: ManagerSession) -> Future[tuple[cleanup.RetirementIssue, ...]]:
    try:
        return session.executor.submit(manager_retirement.retire_all, session.runtime.retired, "replace")
    except RuntimeError:
        failed: Future[tuple[cleanup.RetirementIssue, ...]] = Future()
        failed.set_result(tuple(cleanup.RetirementIssue(
            runtime_revision=owner.revision, reason="cleanup_unavailable",
        ) for owner in session.runtime.retired))
        return failed


def _publish_candidate(
    session: ManagerSession, pending: PendingPreparation, outcome: PreparationOutcome,
) -> manager.ManagerProgress:
    if outcome.prepared is None:
        message = "preparation returned neither a candidate nor a failure"
        raise RuntimeError(message)
    snapshot = outcome.prepared.snapshot
    with session.services.lease.hold_ownership():
        publication = session.services.registry.publish_snapshot(
            pending.expected_registry_revision, snapshot,
            StoredRegistryCommit(
                session.services.repository, pending.operation, session.services.callbacks.clock(),
                outcome.prepared.resolution,
            ),
        )
    if publication.status == "busy":
        return _progress(session, "busy")
    if publication.status == "stale":
        _fence_candidate(session, outcome)
        return _progress(session, FENCED)
    if session.runtime.active is not None:
        session.runtime.retired += (manager_retirement.retirement_owner(session.runtime.active),)
    session.runtime.active = outcome.prepared
    session.runtime.registry_revision = publication.revision
    session.runtime.pending = None
    schedule_cleanup(session)
    return _progress(session, "published")


def _fence_candidate(session: ManagerSession, outcome: PreparationOutcome) -> None:
    retired = () if outcome.prepared is None else (manager_retirement.retirement_owner(outcome.prepared),)
    manager_state.finish_failed(session.services, session.runtime, lifecycle_operations.LifecycleFailure(
        code="interrupted", detail="The selected runtime state changed before publication.",
    ))
    session.runtime.retired += retired
    session.runtime.mode = FENCED
    schedule_cleanup(session)


def _progress(session: ManagerSession, status: manager.ManagerProgressStatus) -> manager.ManagerProgress:
    available = session.runtime.mode == "running" and (
        session.runtime.active is not None or session.runtime.pending is None
    )
    return manager.ManagerProgress(
        status=status, registry_revision=session.runtime.registry_revision, allow_processing=available,
    )
