# Copyright (c) 2026 Zhambyl Yermagambet
"""Check one observer result and store it with its new observations in one transaction."""

from __future__ import annotations

from typing import TYPE_CHECKING

from baqylau_extension_api.models.documents import Diagnostic
from baqylau_extension_api.models.source_results import PositionedObservation

from domain.extension_jobs import JobState
from extensions import job_states
from extensions.models.observations import ObservationAppend
from repository.contract.extension_observers import ObserverSettlement
from repository.errors import EventIdentityConflictError

if TYPE_CHECKING:
    from baqylau_extension_api.models.observer_results import ObservationJobResult

    from extensions.observer_models import ObserverRun, ObserverStores
    from repository.contract.extension_jobs import ExtensionJob

REJECTED_CODE = "host.observations_rejected"
REJECTED_MESSAGE = "The host rejected the observer's new observations."


def settle_result(
    stores: ObserverStores, observer_run: ObserverRun, job: ExtensionJob, job_result: ObservationJobResult,
) -> ExtensionJob:
    """Store the result and its observations together; a rejected append fails the job.

    Returns:
        The stored job after its final state.

    """
    change = job_states.state_change(job, JobState(job_result.status), job_result=job_result)
    try:
        return stores.observers.settle(ObserverSettlement(change, _append(stores, observer_run, job, job_result)))
    except (EventIdentityConflictError, TypeError, ValueError):
        rejected = Diagnostic(code=REJECTED_CODE, message=REJECTED_MESSAGE)
        return job_states.settle(stores.jobs, job, JobState.FAILED, diagnostic=rejected, job_result=job_result)


def _append(
    stores: ObserverStores, observer_run: ObserverRun, job: ExtensionJob, job_result: ObservationJobResult,
) -> ObservationAppend | None:
    if not job_result.observations:
        return None
    return ObservationAppend(
        extension_id=observer_run.package.extension_id,
        manager_id=observer_run.manager_id,
        runtime_revision=observer_run.package.runtime_revision,
        scope=job.scope,
        observed_at=stores.clock(),
        observations=tuple(
            PositionedObservation(position=f"{job.job_id}:{index}", observation=observation)
            for index, observation in enumerate(job_result.observations)
        ),
    )
