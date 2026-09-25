# Copyright (c) 2026 Zhambyl Yermagambet
"""Read one stored durable extension job."""

from http import HTTPStatus
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from api.extensions import job_service
from api.extensions.job_models import ExtensionJobResponse
from api.extensions.scope_documents import request_scope
from app.provider_extension_jobs import Jobs
from domain.ids import ExtensionJobId

router = APIRouter()


def job_scope(scope: Annotated[str, Query(min_length=1)]) -> str:
    """Collect the checked job scope query parameter.

    Returns:
        The raw scope document.

    """
    return scope


JobScope = Annotated[str, Depends(job_scope)]


@router.get("/api/extensions/{extension_id}/jobs/{job_id}")
def extension_job(
    extension_id: str,
    job_id: str,
    jobs: Jobs,
    scope: JobScope,
) -> ExtensionJobResponse:
    """Read one stored durable job.

    Returns:
        The typed job response.

    Raises:
        HTTPException: If the scope is invalid or the job is absent.

    """
    job = jobs.read(extension_id, request_scope(scope), ExtensionJobId(job_id))
    if job is None:
        raise HTTPException(HTTPStatus.NOT_FOUND, "extension job not found")
    return job_service.job_response(job)
