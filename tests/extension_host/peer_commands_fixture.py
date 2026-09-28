# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep alpha's service access, a real job store, and a recording scheduler for peer command tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from baqylau_extension_api.models import service_jobs
from baqylau_extension_api.runtime import call_grants, worker_models

from extensions import (
    control_policy,
    job_requests,
    job_scheduler_slot,
    peer_jobs as peer_job_host,
    registry_package,
    registry_services,
)
from tests import sqlite_repository_dependencies as repository_dependencies
from tests.extension_api import service_samples as peers
from tests.extension_host import peer_beta_fixture as beta, registry_fixture as fixtures, registry_memory_fixture

if TYPE_CHECKING:
    from repository.impl.sqlite.connection import SqliteDatabase

GRANT_SECONDS = 5.0


@dataclass
class RecordedScheduler:
    """Record scheduled jobs instead of running them."""

    submitted: list[job_requests.JobKey] = field(default_factory=list)

    def submit(self, job_key: job_requests.JobKey) -> None:
        """Record one scheduled job."""
        self.submitted.append(job_key)

    def submit_accepted(self, limit: int) -> int:
        """Schedule nothing more.

        Returns:
            Zero; this double reads no stored jobs.

        """
        assert limit > 0
        return 0


@dataclass(frozen=True)
class PeerCase:
    """Keep the registry, the caller, the job host, and the scheduler together."""

    registry: registry_memory_fixture.MemoryRegistry
    caller: registry_package.RegistryPackage
    ledger: call_grants.HostCallLedger
    peer_jobs: peer_job_host.PeerJobs
    scheduler: RecordedScheduler

    def access(self) -> registry_services.RegistryServiceAccess:
        """Bind service calls to alpha's worker connection.

        Returns:
            The production host callback adapter.

        """
        assert self.caller.environment is not None
        return registry_services.RegistryServiceAccess(
            worker_models.WorkerLoadRequest(manifest=self.caller.manifest, environment=self.caller.environment),
            self.registry, self.ledger, self.peer_jobs,
        )

    def submit(self, command: service_jobs.ServiceCommandRequest) -> service_jobs.ServiceJob:
        """Submit one peer command during a live host call to alpha.

        Returns:
            The accepted job reference.

        """
        assert self.caller.environment is not None
        with self.ledger.root(self.caller.environment, beta.SCOPE, GRANT_SECONDS):
            response = self.access().submit_service_command(command)
        assert isinstance(response, service_jobs.ServiceJob)
        return response


def a_case(main: SqliteDatabase, effect: Literal["read", "write"] = "read", *, read_only: bool = False) -> PeerCase:
    """Publish alpha and beta, and bind a real job store.

    Returns:
        The peer command case.

    """
    registry = registry_memory_fixture.MemoryRegistry("initial")
    caller = fixtures.peer(peers.ALPHA)
    published = registry.publish_snapshot(0, fixtures.snapshot(caller, beta.beta_package(effect)))
    assert published.status == "accepted"
    scheduler = RecordedScheduler()
    jobs = peer_job_host.PeerJobs(
        jobs=repository_dependencies.SqliteExtensionJobRepository(main),
        policy=control_policy.ExtensionControlPolicy(read_only=read_only),
        scheduler=job_scheduler_slot.JobSchedulerSlot(scheduler),
    )
    return PeerCase(registry, caller, call_grants.HostCallLedger(), jobs, scheduler)
