# Copyright (c) 2026 Zhambyl Yermagambet
"""Read one stored durable extension job."""

from http import HTTPStatus
from typing import Annotated

from baqylau_extension_api.models.scopes import ExtensionScope
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import TypeAdapter, ValidationError

from api.extensions import job_service
from api.extensions.job_models import ExtensionJobResponse
from app.provider_extension_jobs import Jobs

router = APIRouter()
SCOPE_ADAPTER: TypeAdapter[ExtensionScope] = TypeAdapter(ExtensionScope)


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
    try:
        selected = SCOPE_ADAPTER.validate_json(scope)
    except ValidationError as error:
        raise HTTPException(HTTPStatus.BAD_REQUEST, "scope must be a valid extension scope document") from error
    job = jobs.read(extension_id, selected, job_id)
    if job is None:
        raise HTTPException(HTTPStatus.NOT_FOUND, "extension job not found")
    return job_service.job_response(job)
