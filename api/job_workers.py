# Copyright (c) 2026 Zhambyl Yermagambet
"""Run the durable extension job executor beside the daemon workers."""

from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING

from app import (
    provider_extension_executor,
    provider_extension_jobs,
    provider_extension_policy,
    provider_extension_registry,
    provider_extension_sources,
)
from app.injection import Instances, resolve
from extensions import job_control
from extensions.job_executor import JobExecutor, open_job_executor
from extensions.job_executor_services import JobExecutorServices

if TYPE_CHECKING:
    from collections.abc import Iterator

    from extensions.manager_contract import ExtensionManager

RECOVERY_JOB_LIMIT = 1000


@contextmanager
def job_executor(instances: Instances, manager: ExtensionManager) -> Iterator[JobExecutor]:
    """Recover interrupted jobs, then attach one executor to the scheduler slot until exit.

    Yields:
        The attached executor.

    """
    stores = resolve(instances, provider_extension_jobs.observer_stores)
    job_control.recover_jobs(stores.jobs, RECOVERY_JOB_LIMIT)
    executor = open_job_executor(JobExecutorServices(
        registry=resolve(instances, provider_extension_registry.extension_registry),
        stores=stores,
        manager=manager,
        policy=resolve(instances, provider_extension_policy.extension_control_policy),
        scopes=resolve(instances, provider_extension_sources.extension_scope_registry),
    ))
    scheduler = resolve(instances, provider_extension_executor.job_scheduler)
    scheduler.attach(executor)
    try:
        yield executor
    finally:
        scheduler.attach(None)
        executor.close()
