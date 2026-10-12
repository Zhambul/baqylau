# Copyright (c) 2026 Zhambyl Yermagambet
"""Schedule only the jobs of active packages, and record a job run that fails.

The engine submits the accepted jobs again after each batch of facts. A job of
a package that is not active stayed in that schedule, so it ran again after
every batch, and a failed run left no record.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from api.extensions import command_service
from domain.extension_jobs import JobState
from domain.ids import ExtensionJobId
from extensions.job_requests import JobKey
from repository.contract.extension_jobs import CommandJobRequest
from tests import (
    command_job_fixture as jobs_fixture,
    command_package_fixture as packages,
    job_executor_fixture,
    slow_command_fixture as slow,
)
from tests.extension_host import registry_fixture
from tests.extension_host.registry_memory_fixture import MemoryRegistry

if TYPE_CHECKING:
    from contextlib import AbstractContextManager

    from tests import sqlite_repository_dependencies as repository_dependencies

OTHER_OWNER = "test.other"


class _BrokenRegistry:
    def read_snapshot(self) -> AbstractContextManager[object]:
        message = "the registry is closed"
        raise RuntimeError(message)


def _accept(dispatch: command_service.CommandDispatch) -> ExtensionJobId:
    accepted = command_service.accept_command(
        dispatch, packages.OWNER, packages.COMMAND_ID, packages.SCOPE, packages.a_request(),
    )
    return accepted.job_id


def test_other_owners_do_not_hold_back_a_job(main: repository_dependencies.SqliteDatabase) -> None:
    """An older job of another owner does not take the place of an active owner's job."""
    jobs = jobs_fixture.job_store(main)
    jobs.accept_command(CommandJobRequest(
        owner=OTHER_OWNER, scope=packages.SCOPE, job_id=ExtensionJobId("older"),
        request_key="older", binding="{}", request="{}",
    ))
    accepted = _accept(jobs_fixture.dispatch(main, packages.FakeCommands()))

    scheduled = jobs.jobs_in_state(JobState.ACCEPTED, 1, frozenset((packages.OWNER,)))

    assert [job.job_id for job in scheduled] == [accepted]


def test_schedule_skips_an_inactive_package(main: repository_dependencies.SqliteDatabase) -> None:
    """The job of a package that is not active is not submitted again after a batch."""
    case = slow.a_slow_case()
    _accept(command_service.CommandDispatch((case.package,), jobs_fixture.job_store(main)))
    registry = MemoryRegistry("initial")
    snapshot = registry_fixture.snapshot(registry_fixture.inactive(case.package))
    assert registry.publish_snapshot(0, snapshot).status == "accepted"
    waiting = job_executor_fixture.an_executor(registry, jobs_fixture.stores(main))

    assert waiting.submit_accepted(10) == 0
    waiting.close()


def test_failed_job_run_is_audited(main: repository_dependencies.SqliteDatabase) -> None:
    """An error in a job run leaves one audit row, not only a job that waits."""
    executor = job_executor_fixture.an_executor(_BrokenRegistry(), jobs_fixture.stores(main))

    executor.submit(JobKey(packages.OWNER, packages.SCOPE, ExtensionJobId("job")))
    executor.close()

    audit = executor.services.failures.audit
    assert [func for func, _context in getattr(audit, "errors", ())] == ["extension job (job run)"]
