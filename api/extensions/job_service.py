# Copyright (c) 2026 Zhambyl Yermagambet
"""Map one stored extension job onto its typed API response."""

from baqylau_extension_api.models.command_results import CommandResult
from baqylau_extension_api.models.documents import Diagnostic
from baqylau_extension_api.models.observer_results import ObservationJobResult
from pydantic import TypeAdapter

from api.extensions.job_models import ExtensionJobResponse
from repository.contract.extension_jobs import ExtensionJob


class JobNotFoundError(LookupError):
    """Reject a read for an absent job."""


def job_response(job: ExtensionJob) -> ExtensionJobResponse:
    """Map one stored job onto the typed response.

    Returns:
        The typed job response.

    """
    return ExtensionJobResponse(
        job_id=job.job_id,
        kind=job.kind,
        state=job.state,
        revision=job.revision,
        consumer_cursor=job.consumer_cursor,
        result=_result(job),
        diagnostic=None if job.diagnostic is None else TypeAdapter(Diagnostic).validate_json(job.diagnostic),
    )


def _result(job: ExtensionJob) -> CommandResult | ObservationJobResult | None:
    if job.result is None:
        return None
    if job.kind == "command":
        return TypeAdapter(CommandResult).validate_json(job.result)
    return TypeAdapter(ObservationJobResult).validate_json(job.result)
