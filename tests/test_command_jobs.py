# Copyright (c) 2026 Zhambyl Yermagambet
"""Run accepted command jobs outside the request, and recover them after a restart."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from api.extensions import command_service
from domain.extension_jobs import JobState
from extensions import command_dispatch as command_dispatch_module
from extensions.job_control import recover_jobs
from extensions.job_requests import JobKey
from extensions.models.registry import RuntimeSettings
from repository.contract.extension_jobs import ExtensionJob, ExtensionJobRepository, JobStateChange
from tests import (
    command_job_fixture as jobs_fixture,
    command_package_fixture as packages,
    job_executor_fixture,
)

if TYPE_CHECKING:
    from tests import sqlite_repository_dependencies as repository_dependencies

OWNER = packages.OWNER
COMMAND_ID = packages.COMMAND_ID
SCOPE = packages.SCOPE


def accept(command_dispatch: command_service.CommandDispatch, request_key: str = packages.REQUEST_KEY) -> ExtensionJob:
    """Accept one command request without running it.

    Returns:
        The accepted job.

    """
    return command_service.accept_command(
        command_dispatch, OWNER, COMMAND_ID, SCOPE, packages.a_request(request_key),
    )


def stored_state(jobs: ExtensionJobRepository, job: ExtensionJob) -> JobState | None:
    """Read the stored state of one job.

    Returns:
        The state, or None when the job has no row.

    """
    stored = jobs.read(OWNER, SCOPE, job.job_id)
    return None if stored is None else stored.state


def test_command_executor_runs_an_accepted_job(
    main: repository_dependencies.SqliteDatabase,
) -> None:
    """The executor runs one accepted job outside the request."""
    fake_commands = packages.FakeCommands()
    package = packages.a_package(fake_commands)
    command_dispatch = command_service.CommandDispatch((package,), jobs_fixture.job_store(main))
    accepted = accept(command_dispatch)

    with job_executor_fixture.open_executor(packages.FakeRegistry((package,)), main) as executor:
        executor.submit(JobKey(OWNER, SCOPE, accepted.job_id))

    assert stored_state(command_dispatch.jobs, accepted) == JobState.SUCCEEDED
    assert len(fake_commands.requests) == 1


def test_recover_jobs_marks_only_running_jobs(
    main: repository_dependencies.SqliteDatabase,
) -> None:
    """A restart marks a running job as outcome unknown and keeps a never-started job accepted."""
    command_dispatch = jobs_fixture.dispatch(main, packages.FakeCommands())
    waiting = accept(command_dispatch, "r1")
    started = accept(command_dispatch, "r2")
    jobs = command_dispatch.jobs
    jobs.update_state(JobStateChange(
        owner=OWNER, scope=SCOPE, job_id=started.job_id, expected_revision=1, state=JobState.RUNNING,
    ))

    assert recover_jobs(jobs, 10) == 1

    assert stored_state(jobs, waiting) == JobState.ACCEPTED
    assert stored_state(jobs, started) == JobState.OUTCOME_UNKNOWN


def test_changed_settings_reject_the_command(main: repository_dependencies.SqliteDatabase) -> None:
    """A command accepted before a settings change fails without a call (C23)."""
    fake_commands = packages.FakeCommands()
    accepted = accept(jobs_fixture.dispatch(main, fake_commands))
    changed = replace(packages.a_package(fake_commands), settings=RuntimeSettings(revision=1))

    changed_dispatch = command_service.CommandDispatch((changed,), jobs_fixture.job_store(main))
    job = command_dispatch_module.execute_command_job(changed_dispatch, accepted)

    assert job.state == JobState.FAILED
    assert job.diagnostic is not None
    assert command_dispatch_module.STALE_CODE in job.diagnostic
    assert not fake_commands.requests
