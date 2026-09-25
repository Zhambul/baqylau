# Copyright (c) 2026 Zhambyl Yermagambet
"""Check the host policy and the cause chain, then run one accepted observer job outside the engine thread."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from baqylau_extension_api.models.documents import Diagnostic
from baqylau_extension_api.models.observer_jobs import ObservationJobBinding, ObservationJobRequest
from baqylau_extension_api.observers.registration import observer_selection

from domain.extension_jobs import JobState
from domain.ids import CanonicalEventId
from extensions import job_states, observer_calls, observer_packages, observer_settlement
from extensions.observer_models import ObserverRun

if TYPE_CHECKING:
    from extensions.observer_models import ObserverJobContext, ObserverStores
    from repository.contract.extension_jobs import ExtensionJob, JobStateChange

UNKNOWN_CODE = "host.reply_lost"
UNKNOWN_MESSAGE = "The observer call ended without a checked result, so its outcome is not known."
READ_ONLY_CODE = "host.read_only"
READ_ONLY_MESSAGE = "The host is read-only, so the write observer did not run."
CHAIN_LIMIT_CODE = "host.observer_chain_limit"
CHAIN_LIMIT_MESSAGE = "The trigger has too many observer steps behind it, so the observer did not run."
OBSERVER_CHAIN_LIMIT = 8


def run_observer_job(stores: ObserverStores, context: ObserverJobContext, accepted: ExtensionJob) -> ExtensionJob:
    """Check the host policy and the cause chain, then run one accepted observer job.

    Returns:
        The stored job after its final state; an absent observer leaves the job accepted.

    """
    try:
        package = observer_packages.observer_package(context.packages, accepted.owner)
    except observer_packages.ObserverNotFoundError:
        return accepted
    binding = ObservationJobBinding.model_validate_json(accepted.binding)
    if observer_selection(package.manifest, binding).effect == "write" and context.policy.read_only:
        refused = Diagnostic(code=READ_ONLY_CODE, message=READ_ONLY_MESSAGE)
        return job_states.settle(stores.jobs, accepted, JobState.FAILED, diagnostic=refused)
    cause = CanonicalEventId(binding.event_id)
    if stores.observers.cause_depth(cause, binding.history_revision, OBSERVER_CHAIN_LIMIT) >= OBSERVER_CHAIN_LIMIT:
        refused = Diagnostic(code=CHAIN_LIMIT_CODE, message=CHAIN_LIMIT_MESSAGE)
        return job_states.settle(stores.jobs, accepted, JobState.FAILED, diagnostic=refused)
    return execute_observer_job(stores, ObserverRun(package, context.manager_id), accepted)


def execute_observer_job(stores: ObserverStores, observer_run: ObserverRun, accepted: ExtensionJob) -> ExtensionJob:
    """Claim one accepted job, run observe once, and settle its checked result.

    A request from an older runtime or settings revision is rebuilt for the
    current ones. The claim stores the request that runs.

    Returns:
        The stored job after its final state, or the current job when another run claimed it.

    """
    package = observer_run.package
    request = _current_request(package, accepted)
    running = job_states.claim(stores.jobs, _claim(accepted, request))
    if running is None:
        return job_states.current(stores.jobs, accepted)
    try:
        job_result = observer_calls.checked_result(package, request.binding, package.observer.observe(request))
    except Exception:  # noqa: BLE001 -- A lost or invalid reply may follow an external write.
        lost = Diagnostic(code=UNKNOWN_CODE, message=UNKNOWN_MESSAGE)
        return job_states.settle(stores.jobs, running, JobState.OUTCOME_UNKNOWN, diagnostic=lost)
    return observer_settlement.settle_result(stores, observer_run, running, job_result)


def _current_request(package: observer_packages.ObserverPackage, accepted: ExtensionJob) -> ObservationJobRequest:
    request = ObservationJobRequest.model_validate_json(accepted.request)
    if observer_calls.is_current(package, request):
        return request
    target = observer_calls.ObservationTarget(
        accepted.scope, request.binding.history_revision, accepted.job_id, request.event,
    )
    return observer_calls.observation_request(package, target)


def _claim(accepted: ExtensionJob, request: ObservationJobRequest) -> JobStateChange:
    return replace(
        job_states.state_change(accepted, JobState.RUNNING),
        binding=request.binding.model_dump_json(),
        request=request.model_dump_json(),
    )
