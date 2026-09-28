# Copyright (c) 2026 Zhambyl Yermagambet
"""Accept, read, and stop public peer commands with the same checks as frontend commands."""

from __future__ import annotations

from dataclasses import dataclass

from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.models import service_jobs, services
from baqylau_extension_api.runtime import service_selection

from domain.ids import ExtensionJobId
from extensions import command_dispatch, command_recovery, peer_callers
from extensions.control_policy import ExtensionControlPolicy
from extensions.job_requests import JobKey
from extensions.job_scheduler_slot import JobSchedulerSlot
from repository.contract.extension_jobs import ExtensionJob, ExtensionJobRepository


@dataclass(frozen=True)
class PeerJobs:
    """Keep the job store, the write policy, and the scheduler for peer commands."""

    jobs: ExtensionJobRepository
    policy: ExtensionControlPolicy
    scheduler: JobSchedulerSlot

    def submit(
        self, caller: peer_callers.PeerCaller, service_command: service_jobs.ServiceCommandRequest,
    ) -> service_jobs.ServiceJobResult:
        """Accept one public peer command as a durable job and schedule it.

        Returns:
            The accepted job reference, or an unavailable or stale service.

        Raises:
            ExtensionContractError: If the command is not public or its arguments use another schema.

        """
        resolution = peer_callers.resolve(caller, service_command)
        if isinstance(resolution, services.ServiceUnavailable):
            return resolution
        if resolution.service_revision != service_command.service_revision:
            return services.ServiceUnavailable(binding=service_command.binding, reason="stale_handle")
        definition = next(
            (command for command in resolution.commands if command.name == service_command.command_id), None,
        )
        if definition is None or definition.arguments != service_command.arguments.schema_ref:
            message = "command is not exposed by this service with these arguments"
            raise ExtensionContractError(message)
        dispatch = command_dispatch.CommandDispatch(caller.snapshot.packages, self.jobs, self.policy)
        accepted = command_dispatch.accept_command(
            dispatch, service_command.binding.owner, service_command.command_id, service_command.binding.scope,
            command_dispatch.CommandSubmission(
                arguments=service_command.arguments.json_text,
                request_key=caller.request_key(service_command.request_key),
                expected_state_revision=service_command.expected_state_revision,
            ),
        )
        self.scheduler.submit(JobKey(accepted.owner, accepted.scope, accepted.job_id))
        return peer_callers.service_job(service_command, accepted)

    def read(
        self, caller: peer_callers.PeerCaller, service_job: service_jobs.ServiceJobRequest,
    ) -> service_jobs.ServiceJobResult:
        """Read one peer job that this caller submitted.

        Returns:
            The stored job state.

        """
        return peer_callers.service_job(service_job, self._job(caller, service_job))

    def cancel(
        self, caller: peer_callers.PeerCaller, service_job_cancel: service_jobs.ServiceJobCancelRequest,
    ) -> service_jobs.ServiceJobCancelResult:
        """Ask the owning peer to stop one attempt that this caller submitted.

        Returns:
            The checked acknowledgment and the stored revision after any proven stop.

        Raises:
            ExtensionContractError: If the stored revision changed.

        """
        job = self._job(caller, service_job_cancel)
        if job.revision != service_job_cancel.expected_revision:
            message = "peer job revision changed"
            raise ExtensionContractError(message)
        stored, result = command_recovery.cancel_command_job(
            caller.snapshot.packages, self.jobs, job, service_job_cancel.reason,
        )
        return service_jobs.ServiceJobCancelled(
            binding=service_job_cancel.binding, job_id=job.job_id, cancel_status=result.status,
            revision=stored.revision,
        )

    def _job(self, caller: peer_callers.PeerCaller, service_job: service_jobs.ServiceJobRequest) -> ExtensionJob:
        binding = service_job.binding
        caller.calls.require_call(caller.worker.environment, binding.scope)
        service_selection.require_service_consumer(caller.worker.manifest, binding)
        job = self.jobs.read(binding.owner, binding.scope, ExtensionJobId(service_job.job_id))
        if job is None or not caller.submitted(job):
            message = "peer job was not submitted by this caller"
            raise ExtensionContractError(message)
        return job
