# Copyright (c) 2026 Zhambyl Yermagambet
"""Run accepted command and observer jobs on one bounded background executor."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass, field
from threading import Lock
from typing import TYPE_CHECKING

from domain.extension_jobs import JobKind, JobState
from extensions import (
    command_dispatch,
    job_executor_services,
    job_requests,
    job_scheduling_contract,
    observer_execution,
    observer_models,
    runtime_identity,
)

if TYPE_CHECKING:
    from baqylau_extension_api.models.scopes import ExtensionScope

    from extensions.registry_snapshot import RuntimeSnapshot
    from extensions.source_scope_contract import ExtensionScopeRegistry

JOB_WORKERS = 1


@dataclass(frozen=True)
class JobExecutor(job_scheduling_contract.JobScheduling):
    """Dispatch accepted jobs outside the request and engine threads."""

    services: job_executor_services.JobExecutorServices
    pool: ThreadPoolExecutor
    queued: set[job_requests.JobKey] = field(default_factory=set)
    lock: Lock = field(default_factory=Lock)

    def submit(self, job_key: job_requests.JobKey) -> None:
        """Schedule one accepted job unless it is already queued."""
        with self.lock:
            if job_key in self.queued:
                return
            self.queued.add(job_key)
        self.pool.submit(self._run, job_key)

    def submit_accepted(self, limit: int) -> int:
        """Schedule the oldest accepted jobs that are not already queued.

        Returns:
            The number of accepted jobs read.

        """
        accepted = self.services.stores.jobs.jobs_in_state(JobState.ACCEPTED, limit)
        for job in accepted:
            self.submit(job_requests.JobKey(job.owner, job.scope, job.job_id))
        return len(accepted)

    def close(self) -> None:
        """Stop accepting work and wait for the running job."""
        self.pool.shutdown(wait=True, cancel_futures=False)

    def _run(self, job_key: job_requests.JobKey) -> None:
        try:
            with _held(self.services.scopes, job_key.scope), self.services.registry.read_snapshot() as read:
                self._dispatch(read.snapshot, job_key)
        except Exception:  # noqa: BLE001 -- Leave the failed job for reconciliation.
            return
        finally:
            with self.lock:
                self.queued.discard(job_key)

    def _dispatch(self, snapshot: RuntimeSnapshot, job_key: job_requests.JobKey) -> None:
        job = self.services.stores.jobs.read(job_key.owner, job_key.scope, job_key.job_id)
        if job is None or job.state != JobState.ACCEPTED:
            return
        if job.kind == JobKind.COMMAND:
            dispatch = command_dispatch.CommandDispatch(
                snapshot.packages, self.services.stores.jobs, self.services.policy,
            )
            command_dispatch.execute_command_job(dispatch, job)
            return
        manager_state = self.services.manager.read_state()
        manager_id = runtime_identity.snapshot_manager_id(manager_state, snapshot.directory.runtime_revision)
        if manager_id is None:
            return
        observer_context = observer_models.ObserverJobContext(snapshot.packages, manager_id, self.services.policy)
        observer_execution.run_observer_job(self.services.stores, observer_context, job)


def open_job_executor(services: job_executor_services.JobExecutorServices) -> JobExecutor:
    """Start one serial job executor; a running job keeps its scope's sources active.

    Returns:
        The executor with its own background thread.

    """
    return JobExecutor(
        services=services,
        pool=ThreadPoolExecutor(max_workers=JOB_WORKERS, thread_name_prefix="baqylau-extension-job"),
    )


def _held(scopes: ExtensionScopeRegistry | None, scope: ExtensionScope) -> AbstractContextManager[object]:
    return nullcontext() if scopes is None else scopes.hold_scope(scope)
