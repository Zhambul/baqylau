# Copyright (c) 2026 Zhambyl Yermagambet
"""Check peer job replies before returning them to an extension."""

from pydantic import TypeAdapter

from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.models.service_jobs import (
    ServiceJob,
    ServiceJobCancelled,
    ServiceJobCancelRequest,
    ServiceJobCancelResult,
    ServiceJobRequest,
    ServiceJobResult,
)
from baqylau_extension_api.models.services import ServiceResolveRequest


def validate_service_job(request: ServiceResolveRequest, response: ServiceJobResult) -> ServiceJobResult:
    """Keep a peer job reference tied to the exact requested service and, for a read, its job.

    Returns:
        A checked job reference or an explicit unavailable service.

    Raises:
        ExtensionContractError: If the reply changes its binding or its job.

    """
    checked = TypeAdapter[ServiceJobResult](ServiceJobResult).validate_python(response)
    if checked.binding != request.binding:
        message = "service job changed its requested binding"
        raise ExtensionContractError(message)
    if _changed_job(request, checked):
        message = "service job changed its requested job"
        raise ExtensionContractError(message)
    return checked


def validate_service_job_cancel(
    request: ServiceJobCancelRequest, response: ServiceJobCancelResult,
) -> ServiceJobCancelResult:
    """Keep a stop acknowledgment tied to the exact requested job.

    Returns:
        A checked acknowledgment or an explicit unavailable service.

    Raises:
        ExtensionContractError: If the reply changes its binding or its job.

    """
    checked = TypeAdapter[ServiceJobCancelResult](ServiceJobCancelResult).validate_python(response)
    if checked.binding != request.binding or (
        isinstance(checked, ServiceJobCancelled) and checked.job_id != request.job_id
    ):
        message = "service job cancellation changed its requested job"
        raise ExtensionContractError(message)
    return checked


def _changed_job(request: ServiceResolveRequest, checked: ServiceJobResult) -> bool:
    if not isinstance(request, ServiceJobRequest) or not isinstance(checked, ServiceJob):
        return False
    return checked.job_id != request.job_id
