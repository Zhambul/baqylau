# Copyright (c) 2026 Zhambyl Yermagambet
"""Run one job executor with one worker over the supplied registry and stores."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, contextmanager
from typing import TYPE_CHECKING, cast

from extensions.control_policy import ExtensionControlPolicy
from extensions.job_executor import JobExecutor
from extensions.job_executor_services import JobExecutorServices
from tests import command_job_fixture as jobs_fixture

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from extensions import observer_models, registry_contract, runtime_identity
    from repository.impl.sqlite.connection import SqliteDatabase


def an_executor(
    registry: object,
    observer_stores: observer_models.ObserverStores,
    manager: object = None,
    policy: ExtensionControlPolicy | None = None,
) -> JobExecutor:
    """Build the executor with one worker thread; with no manager, no manager is committed.

    Returns:
        The executor; the caller closes it.

    """
    services = JobExecutorServices(
        registry=cast("registry_contract.ExtensionRegistry", registry),
        stores=observer_stores,
        manager=cast("runtime_identity.ManagerStateReads", manager),
        policy=ExtensionControlPolicy() if policy is None else policy,
    )
    return JobExecutor(services=services, pool=ThreadPoolExecutor(max_workers=1))


@contextmanager
def open_executor(
    registry: object, main: SqliteDatabase, release: Callable[[], object] | None = None,
) -> Iterator[JobExecutor]:
    """Run the executor over the real job stores, and close it at the end.

    The release callback runs before the close, so a blocked job can finish.

    Yields:
        The running executor.

    """
    executor = an_executor(registry, jobs_fixture.stores(main))
    with ExitStack() as cleanup:
        cleanup.callback(executor.close)
        if release is not None:
            cleanup.callback(release)
        yield executor
