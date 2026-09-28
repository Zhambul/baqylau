# Copyright (c) 2026 Zhambyl Yermagambet
"""Store the state changes of one durable job from its exact revision."""

from baqylau_extension_api.models.documents import Diagnostic
from pydantic import BaseModel

from domain.extension_jobs import JobState
from repository.contract.extension_jobs import ExtensionJob, ExtensionJobRepository, JobStateChange


def state_change(
    job: ExtensionJob,
    state: JobState,
    *,
    diagnostic: Diagnostic | None = None,
    job_result: BaseModel | None = None,
) -> JobStateChange:
    """Name one change from the job's stored revision.

    Returns:
        The change with the encoded result and diagnostic.

    """
    return JobStateChange(
        owner=job.owner, scope=job.scope, job_id=job.job_id, expected_revision=job.revision, state=state,
        result=None if job_result is None else job_result.model_dump_json(),
        diagnostic=None if diagnostic is None else diagnostic.model_dump_json(),
    )


def claim(jobs: ExtensionJobRepository, change: JobStateChange) -> ExtensionJob | None:
    """Store a claim to run.

    Returns:
        The running job, or None when another writer changed the job first.

    """
    try:
        return jobs.update_state(change)
    except ValueError:
        return None


def settle(
    jobs: ExtensionJobRepository,
    job: ExtensionJob,
    state: JobState,
    *,
    diagnostic: Diagnostic | None = None,
    job_result: BaseModel | None = None,
) -> ExtensionJob:
    """Store one final state from the job's exact revision.

    Returns:
        The stored job, or the current job when another writer changed it first.

    """
    stored = claim(jobs, state_change(job, state, diagnostic=diagnostic, job_result=job_result))
    return current(jobs, job) if stored is None else stored


def current(jobs: ExtensionJobRepository, job: ExtensionJob) -> ExtensionJob:
    """Read the stored job again after a lost revision race.

    Returns:
        The stored job, or the given job when it is not stored.

    """
    stored = jobs.read(job.owner, job.scope, job.job_id)
    return job if stored is None else stored
