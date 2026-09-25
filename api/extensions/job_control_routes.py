# Copyright (c) 2026 Zhambyl Yermagambet
"""Cancel and reconcile one stored durable extension job."""

from fastapi import APIRouter, Depends

from api.extensions import job_control_service as job_control, job_service
from api.extensions.admission import require_extension_json
from api.extensions.job_models import (
    ExtensionJobCancelRequest,
    ExtensionJobCancelResponse,
    ExtensionJobReconcileRequest,
    ExtensionJobResponse,
)
from api.extensions.scope_documents import request_scope
from domain.ids import ExtensionJobId
from extensions.job_requests import JobCancelSubmission, JobKey, JobReconcileSubmission

router = APIRouter()


@router.post(
    "/api/extensions/{extension_id}/jobs/{job_id}/cancel",
    dependencies=[Depends(require_extension_json)],
)
def cancel_extension_job(
    extension_id: str,
    job_id: str,
    extension_job_cancel_request: ExtensionJobCancelRequest,
    services: job_control.JobControlDependencies,
) -> ExtensionJobCancelResponse:
    """Request cancellation of one stored command or observer attempt.

    Returns:
        The checked cancellation request state.

    """
    job_key = JobKey(extension_id, request_scope(extension_job_cancel_request.scope), ExtensionJobId(job_id))
    submission = JobCancelSubmission(
        expected_revision=extension_job_cancel_request.expected_revision, reason=extension_job_cancel_request.reason,
    )
    outcome = job_control.control(services, lambda control: control.cancel(job_key, submission))
    return ExtensionJobCancelResponse(status=outcome.status, revision=outcome.revision, diagnostic=outcome.diagnostic)


@router.post(
    "/api/extensions/{extension_id}/jobs/{job_id}/reconcile",
    dependencies=[Depends(require_extension_json)],
)
def reconcile_extension_job(
    extension_id: str,
    job_id: str,
    extension_job_reconcile_request: ExtensionJobReconcileRequest,
    services: job_control.JobControlDependencies,
) -> ExtensionJobResponse:
    """Inspect an uncertain attempt without repeating its original call.

    Returns:
        The stored job after the reconciled outcome.

    """
    job_key = JobKey(extension_id, request_scope(extension_job_reconcile_request.scope), ExtensionJobId(job_id))
    submission = JobReconcileSubmission(
        expected_revision=extension_job_reconcile_request.expected_revision,
        receipt=extension_job_reconcile_request.receipt,
    )
    job = job_control.control(services, lambda control: control.reconcile(job_key, submission))
    return job_service.job_response(job)
