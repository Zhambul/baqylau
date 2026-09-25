# Copyright (c) 2026 Zhambyl Yermagambet
"""Cancel, reconcile, and recover stored command and observer jobs."""

from __future__ import annotations

from dataclasses import dataclass

from baqylau_extension_api.models.documents import Diagnostic

from domain.extension_jobs import JobCancelStatus, JobKind, JobState
from extensions import command_recovery, observer_recovery
from extensions.job_requests import JobCancelOutcome, JobCancelSubmission, JobKey, JobReconcileSubmission
from extensions.observer_models import ObserverRun, ObserverStores
from extensions.observer_packages import observer_package
from extensions.registry_package import RegistryPackage
from repository.contract.extension_jobs import ExtensionJob, ExtensionJobRepository, JobStateChange

RECOVERY_CODE = "host.job_recovered"
RECOVERY_MESSAGE = "The daemon restarted before the job produced a checked result."


class JobNotFoundError(LookupError):
    """Reject a job operation for an absent job."""


class JobRevisionError(RuntimeError):
    """Reject a job operation whose exact revision no longer matches."""


class RuntimeIdentityError(RuntimeError):
    """Reject observer settlement when no committed manager owns the borrowed runtime."""


@dataclass(frozen=True)
class JobControl:
    """Keep the active packages, the stores, and the committing manager for one request."""

    packages: tuple[RegistryPackage, ...]
    stores: ObserverStores
    manager_id: str | None

    def cancel(self, job_key: JobKey, submission: JobCancelSubmission) -> JobCancelOutcome:
        """Ask the owning capability to stop one stored attempt.

        Returns:
            The checked acknowledgment and the stored revision after any proven stop.

        """
        job = self._job(job_key, submission.expected_revision)
        if job.kind == JobKind.COMMAND:
            stored, command_result = command_recovery.cancel_command_job(
                self.packages, self.stores.jobs, job, submission.reason,
            )
            return JobCancelOutcome(JobCancelStatus(command_result.status), stored.revision, command_result.diagnostic)
        package = observer_package(self.packages, job.owner)
        stored, observer_result = observer_recovery.cancel_observer_job(self.stores, package, job, submission.reason)
        return JobCancelOutcome(JobCancelStatus(observer_result.status), stored.revision, observer_result.diagnostic)

    def reconcile(self, job_key: JobKey, submission: JobReconcileSubmission) -> ExtensionJob:
        """Inspect an uncertain attempt without repeating its original call.

        Returns:
            The stored job after the reconciled outcome.

        Raises:
            RuntimeIdentityError: If an observer result cannot be bound to the committed runtime.

        """
        job = self._job(job_key, submission.expected_revision)
        if job.kind == JobKind.COMMAND:
            return command_recovery.reconcile_command_job(self.packages, self.stores.jobs, job, submission.receipt)
        if self.manager_id is None:
            message = "no committed manager owns the active runtime"
            raise RuntimeIdentityError(message)
        observer_run = ObserverRun(observer_package(self.packages, job.owner), self.manager_id)
        return observer_recovery.reconcile_observer_job(self.stores, observer_run, job, submission.receipt)

    def _job(self, job_key: JobKey, expected_revision: int | None) -> ExtensionJob:
        job = self.stores.jobs.read(job_key.owner, job_key.scope, job_key.job_id)
        if job is None:
            message = "extension job not found"
            raise JobNotFoundError(message)
        if expected_revision is not None and job.revision != expected_revision:
            message = "extension job revision changed"
            raise JobRevisionError(message)
        return job


def recover_jobs(extension_job_repository: ExtensionJobRepository, limit: int) -> int:
    """Mark every job that was running at restart as outcome unknown.

    An accepted job never started: its claim to running comes before any
    capability call. It stays accepted, and the executor schedules it again.

    Returns:
        The number of recovered jobs.

    """
    recovered = 0
    for job in extension_job_repository.jobs_in_state(JobState.RUNNING, limit):
        try:
            extension_job_repository.update_state(JobStateChange(
                owner=job.owner,
                scope=job.scope,
                job_id=job.job_id,
                expected_revision=job.revision,
                state=JobState.OUTCOME_UNKNOWN,
                diagnostic=Diagnostic(code=RECOVERY_CODE, message=RECOVERY_MESSAGE).model_dump_json(),
            ))
        except ValueError:
            continue
        recovered += 1
    return recovered
