# Copyright (c) 2026 Zhambyl Yermagambet
"""Bind one peer caller to its connection and map its stored jobs to service replies."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from baqylau_extension_api.models import service_jobs, services
from baqylau_extension_api.models.command_results import CommandResult
from baqylau_extension_api.models.documents import Diagnostic
from baqylau_extension_api.runtime import service_selection
from baqylau_extension_api.runtime.call_grants import HostCallLedger
from baqylau_extension_api.runtime.worker_models import WorkerLoadRequest
from pydantic import TypeAdapter

from extensions.registry_snapshot import RuntimeSnapshot

if TYPE_CHECKING:
    from repository.contract.extension_jobs import ExtensionJob

_RESULT_ADAPTER: TypeAdapter[CommandResult] = TypeAdapter(CommandResult)


@dataclass(frozen=True)
class PeerCaller:
    """Bind one worker connection, its live call ledger, and the borrowed runtime set."""

    worker: WorkerLoadRequest
    calls: HostCallLedger
    snapshot: RuntimeSnapshot

    @property
    def extension_id(self) -> str:
        """The calling package, taken from its connection, never from the payload."""
        return self.worker.environment.extension_info.extension_id

    def request_key(self, request_key: str) -> str:
        """Keep each caller's duplicate detection separate from frontends and other callers.

        Returns:
            The stored request key of this caller's peer command.

        """
        return f"peer:{self.extension_id}:{request_key}"

    def submitted(self, extension_job: ExtensionJob) -> bool:
        """Tell whether this caller submitted the job.

        Returns:
            True when the job's request key has this caller's prefix.

        """
        stored_key = extension_job.request_key
        return stored_key is not None and stored_key.startswith(self.request_key(""))


def resolve(
    caller: PeerCaller, service_command: service_jobs.ServiceCommandRequest,
) -> services.ServiceResolved | services.ServiceUnavailable:
    """Resolve the provider of one peer command for this caller.

    Returns:
        The resolved service, or the reason that it is unavailable.

    """
    caller.calls.require_call(caller.worker.environment, service_command.binding.scope)
    provider = caller.snapshot.get_service_provider(service_command.binding.owner, service_command.binding.scope)
    return service_selection.resolve_provider(caller.worker.manifest, service_command.binding, provider)


def service_job(
    request: service_jobs.ServiceJobRequest | service_jobs.ServiceCommandRequest, job: ExtensionJob,
) -> service_jobs.ServiceJob:
    """Map one stored peer job to its service reply.

    Returns:
        The job state, result, and diagnostic.

    """
    return service_jobs.ServiceJob(
        binding=request.binding,
        job_id=job.job_id,
        state=job.state.value,
        revision=job.revision,
        result=None if job.result is None else _RESULT_ADAPTER.validate_json(job.result),
        diagnostic=None if job.diagnostic is None else Diagnostic.model_validate_json(job.diagnostic),
    )
