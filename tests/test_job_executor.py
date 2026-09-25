# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep engine reads and command admission open while a slow job runs (C11)."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

from api.extensions import command_service
from domain.extension_jobs import JobState
from extensions.job_requests import JobKey
from extensions.registry_snapshot import prepare_snapshot
from tests import (
    command_job_fixture as jobs_fixture,
    command_package_fixture as packages,
    job_executor_fixture,
    slow_command_fixture as slow,
)

if TYPE_CHECKING:
    from tests import sqlite_repository_dependencies as repository_dependencies
    from tests.extension_host.registry_memory_fixture import MemoryRegistry

READ_SECONDS = 1.0
OWNER = packages.OWNER
SCOPE = packages.SCOPE


def check_open_while_running(registry: MemoryRegistry, command_dispatch: command_service.CommandDispatch) -> None:
    """Read on another thread, admit a second command, and fail to publish during the shared read."""
    with ThreadPoolExecutor(max_workers=1) as engine, registry.read_snapshot() as read:
        assert engine.submit(_read_packages, registry).result(timeout=READ_SECONDS) == 1
        second = command_service.accept_command(
            command_dispatch, OWNER, packages.COMMAND_ID, SCOPE, packages.a_request("request-2"),
        )
        assert second.state == JobState.ACCEPTED
        assert registry.publish_snapshot(read.revision, prepare_snapshot(2, "runtime-two", ())).status == "busy"


def test_slow_job_keeps_reads_and_admission_open(main: repository_dependencies.SqliteDatabase) -> None:
    """A running job holds only a shared read; reads and new commands continue, and reload waits."""
    case = slow.a_slow_case()
    command_dispatch = command_service.CommandDispatch((case.package,), jobs_fixture.job_store(main))
    first = command_service.accept_command(command_dispatch, OWNER, packages.COMMAND_ID, SCOPE, packages.a_request())

    with job_executor_fixture.open_executor(case.registry, main, case.slow_commands.release.set) as executor:
        executor.submit(JobKey(OWNER, SCOPE, first.job_id))
        assert case.slow_commands.started.wait(slow.WAIT_SECONDS)
        check_open_while_running(case.registry, command_dispatch)

    succeeded = command_dispatch.jobs.jobs_in_state(JobState.SUCCEEDED, 10)
    assert [job.job_id for job in succeeded] == [first.job_id]


def _read_packages(registry: MemoryRegistry) -> int:
    with registry.read_snapshot() as read:
        return len(read.snapshot.packages)
