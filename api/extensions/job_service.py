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


def job_response(extension_job: ExtensionJob) -> ExtensionJobResponse:
    """Map one stored job onto the typed response.

    Returns:
        The typed job response.

    """
    return ExtensionJobResponse(
        job_id=extension_job.job_id,
        kind=extension_job.kind,
        state=extension_job.state,
        revision=extension_job.revision,
        consumer_cursor=extension_job.consumer_cursor,
        result=_result(extension_job),
        diagnostic=(
            None if extension_job.diagnostic is None
            else TypeAdapter(Diagnostic).validate_json(extension_job.diagnostic)
        ),
    )


def _result(extension_job: ExtensionJob) -> CommandResult | ObservationJobResult | None:
    if extension_job.result is None:
        return None
    if extension_job.kind == "command":
        return TypeAdapter(CommandResult).validate_json(extension_job.result)
    return TypeAdapter(ObservationJobResult).validate_json(extension_job.result)
