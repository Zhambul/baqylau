# Copyright (c) 2026 Zhambyl Yermagambet
"""Cancel and reconcile stored command jobs through job control."""

from __future__ import annotations

from typing import TYPE_CHECKING, NoReturn

import pytest

from api.extensions import command_service
from domain.extension_jobs import JobCancelStatus, JobState
from domain.ids import ExtensionJobId
from extensions.job_control import JobNotFoundError, JobRevisionError
from extensions.job_requests import JobCancelSubmission, JobKey, JobReconcileSubmission
from tests import command_job_fixture as jobs_fixture, command_package_fixture as packages
from tests.extension_api import operation_samples

if TYPE_CHECKING:
    from baqylau_extension_api.models.commands import CommandRequest

    from tests import sqlite_repository_dependencies as repository_dependencies

OWNER = packages.OWNER
SCOPE = packages.SCOPE
STOP = JobCancelSubmission(expected_revision=1, reason="stop")
CANCELED_REVISION = 2
STALE_REVISION = 99


class LostReplyCommands(packages.FakeCommands):
    """Do the external write, then lose the reply."""

    def execute(self, request: CommandRequest) -> NoReturn:
        """Record the write and fail before any reply reaches the host.

        Raises:
            ConnectionError: Always, after the write.

        """
        self.requests.append(request)
        message = "reply lost"
        raise ConnectionError(message)


def test_cancel_command_marks_a_proven_stop(
    main: repository_dependencies.SqliteDatabase,
) -> None:
    """A proven stop sets the stored job state to canceled."""
    job_control = jobs_fixture.control(main, packages.FakeCommands(cancel_status="canceled"))
    binding = operation_samples.command_request().binding
    jobs_fixture.seed_job(main, binding)
    job_key = JobKey(OWNER, SCOPE, ExtensionJobId(binding.job_id))

    response = job_control.cancel(job_key, STOP)

    assert (response.status, response.revision) == (JobCancelStatus.CANCELED, CANCELED_REVISION)
    stored = job_control.stores.jobs.read(OWNER, SCOPE, job_key.job_id)
    assert stored is not None
    assert stored.state == JobState.CANCELED


def test_cancel_command_rejects_a_stale_revision(
    main: repository_dependencies.SqliteDatabase,
) -> None:
    """A cancellation from an old revision is refused."""
    binding = operation_samples.command_request().binding
    jobs_fixture.seed_job(main, binding)
    stale = JobCancelSubmission(expected_revision=STALE_REVISION, reason="stop")

    with pytest.raises(JobRevisionError):
        jobs_fixture.control(main, packages.FakeCommands()).cancel(
            JobKey(OWNER, SCOPE, ExtensionJobId(binding.job_id)), stale,
        )


def test_cancel_command_reports_a_missing_job(
    main: repository_dependencies.SqliteDatabase,
) -> None:
    """An absent job identity is a not-found error."""
    with pytest.raises(JobNotFoundError):
        jobs_fixture.control(main, packages.FakeCommands()).cancel(
            JobKey(OWNER, SCOPE, ExtensionJobId("missing")), STOP,
        )


def test_reconcile_stores_a_resolved_outcome(
    main: repository_dependencies.SqliteDatabase,
) -> None:
    """Reconciliation stores the checked outcome without running the command again."""
    fake_commands = packages.FakeCommands()
    request = operation_samples.command_request()
    jobs_fixture.seed_job(main, request.binding, request.model_dump_json())

    job = jobs_fixture.control(main, fake_commands).reconcile(
        JobKey(OWNER, SCOPE, ExtensionJobId(request.binding.job_id)), JobReconcileSubmission(expected_revision=1),
    )

    assert job.state == JobState.SUCCEEDED
    assert job.result is not None
    assert not fake_commands.requests


def test_lost_reply_reconciles_without_a_rewrite(
    main: repository_dependencies.SqliteDatabase,
) -> None:
    """A lost reply after a write is outcome unknown; reconciliation resolves it without a second write (C22)."""
    fake_commands = LostReplyCommands()
    command_dispatch = jobs_fixture.dispatch(main, fake_commands)
    lost = command_service.run_command(command_dispatch, OWNER, packages.COMMAND_ID, SCOPE, packages.a_request())

    assert lost.state == JobState.OUTCOME_UNKNOWN
    resolved = jobs_fixture.control(main, fake_commands).reconcile(
        JobKey(OWNER, SCOPE, lost.job_id), JobReconcileSubmission(expected_revision=lost.revision),
    )

    assert resolved.state == JobState.SUCCEEDED
    assert len(fake_commands.requests) == 1
