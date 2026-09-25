# Copyright (c) 2026 Zhambyl Yermagambet
"""Stop or reconcile one stored observer job without a second observe call."""

from __future__ import annotations

from typing import TYPE_CHECKING

from baqylau_extension_api.models.observer_jobs import (
    ObservationCancelRequest,
    ObservationJobBinding,
    ObservationJobRequest,
    ObservationReconcileRequest,
)
from baqylau_extension_api.observers import requests as observer_requests, results as observer_results

from domain.extension_jobs import JobCancelStatus, JobState
from extensions import job_states, observer_calls, observer_settlement

if TYPE_CHECKING:
    from baqylau_extension_api.models.documents import EncodedDocument
    from baqylau_extension_api.models.observer_jobs import ObservationCancelResult

    from extensions.observer_models import ObserverRun, ObserverStores
    from extensions.observer_packages import ObserverPackage
    from repository.contract.extension_jobs import ExtensionJob

PROVEN_STOPS = (JobCancelStatus.CANCELED, JobCancelStatus.OUTCOME_UNKNOWN)


def cancel_observer_job(
    stores: ObserverStores, package: ObserverPackage, job: ExtensionJob, reason: str,
) -> tuple[ExtensionJob, ObservationCancelResult]:
    """Ask the observer to stop one attempt and store only a proven stop.

    Returns:
        The stored job and the checked acknowledgment.

    """
    cancel_request = observer_requests.validate_observation_cancel(package.manifest, ObservationCancelRequest(
        binding=ObservationJobBinding.model_validate_json(job.binding), reason=reason,
    ))
    cancel_result = observer_results.validate_observation_cancel_result(
        cancel_request, package.observer.cancel_observation(cancel_request),
    )
    if JobCancelStatus(cancel_result.status) not in PROVEN_STOPS:
        return job, cancel_result
    stopped = JobState(cancel_result.status)
    return job_states.settle(stores.jobs, job, stopped, diagnostic=cancel_result.diagnostic), cancel_result


def reconcile_observer_job(
    stores: ObserverStores, observer_run: ObserverRun, job: ExtensionJob, receipt: EncodedDocument | None,
) -> ExtensionJob:
    """Inspect an uncertain attempt without running observe again, then settle it.

    Returns:
        The stored job after the reconciled outcome.

    """
    package = observer_run.package
    reconcile_request = observer_requests.validate_observation_reconcile(
        package.manifest, package.schemas, ObservationReconcileRequest(
            observation=ObservationJobRequest.model_validate_json(job.request), receipt=receipt,
        ),
    )
    reply = package.observer.reconcile_observation(reconcile_request)
    job_result = observer_calls.checked_result(package, reconcile_request.observation.binding, reply)
    return observer_settlement.settle_result(stores, observer_run, job, job_result)
