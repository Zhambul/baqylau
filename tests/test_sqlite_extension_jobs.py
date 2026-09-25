# Copyright (c) 2026 Zhambyl Yermagambet
"""Accept and advance durable extension jobs."""

from __future__ import annotations

import pytest
from baqylau_extension_api.models import scopes

from domain.extension_jobs import JobState
from domain.ids import CanonicalEventId, ExtensionJobId
from repository.contract.extension_jobs import CommandJobRequest, JobStateChange, ObserverJobRequest
from tests import sqlite_repository_dependencies as repository_dependencies

OWNER = "test.owner"
SCOPE = scopes.InstallationScope()
BINDING = '{"job_id":"job-1","request_key":"r1","call_id":"c1"}'
REQUEST = '{"arguments":{"x":1},"settings_revision":1}'
FIRST_REVISION = 1
SECOND_REVISION = 2
THIRD_REVISION = 3
CONSUMER_CURSOR = 42
STALE_MESSAGE = "extension job changed since it was read"
FIRST_JOB = "job-1"
SECOND_JOB = "job-2"
FIRST_KEY = "r1"
SUCCEEDED_RESULT = '{"status":"succeeded"}'


def command(job_id: str, request_key: str) -> CommandJobRequest:
    """Build one command request.

    Returns:
        The command request.

    """
    return CommandJobRequest(
        owner=OWNER, scope=SCOPE, job_id=ExtensionJobId(job_id),
        request_key=request_key, binding=BINDING, request=REQUEST,
    )


def observer(job_id: str, cause_event_id: str) -> ObserverJobRequest:
    """Build one observer request.

    Returns:
        The observer request.

    """
    return ObserverJobRequest(
        owner=OWNER,
        scope=SCOPE,
        job_id=ExtensionJobId(job_id),
        cause_event_id=CanonicalEventId(cause_event_id),
        binding=BINDING,
        request=REQUEST,
        consumer_cursor=CONSUMER_CURSOR,
    )


def test_command_accept_deduplicates_by_key(
    main: repository_dependencies.SqliteDatabase,
) -> None:
    """A repeated command request key returns the first accepted job."""
    repository = repository_dependencies.SqliteExtensionJobRepository(main)
    first = repository.accept_command(command(FIRST_JOB, FIRST_KEY))
    repeated = repository.accept_command(command(SECOND_JOB, FIRST_KEY))

    assert first.job_id == FIRST_JOB
    assert first.kind == "command"
    assert first.state == "accepted"
    assert first.revision == FIRST_REVISION
    assert repeated.job_id == FIRST_JOB


def test_observer_accept_deduplicates_by_cause(
    main: repository_dependencies.SqliteDatabase,
) -> None:
    """A repeated observer cause returns the first job and keeps its cursor."""
    repository = repository_dependencies.SqliteExtensionJobRepository(main)
    first = repository.accept_observer(observer(FIRST_JOB, "event-1"))
    repeated = repository.accept_observer(observer(SECOND_JOB, "event-1"))

    assert first.kind == "observer"
    assert first.cause_event_id == "event-1"
    assert first.consumer_cursor == CONSUMER_CURSOR
    assert repeated.job_id == FIRST_JOB


def test_update_state_rejects_a_stale_revision(
    main: repository_dependencies.SqliteDatabase,
) -> None:
    """A state change uses the exact revision and rejects an old one."""
    repository = repository_dependencies.SqliteExtensionJobRepository(main)
    repository.accept_command(command(FIRST_JOB, FIRST_KEY))

    running = repository.update_state(JobStateChange(
        owner=OWNER, scope=SCOPE, job_id=ExtensionJobId(FIRST_JOB),
        expected_revision=FIRST_REVISION, state=JobState.RUNNING,
    ))
    assert (running.state, running.revision) == ("running", SECOND_REVISION)

    with pytest.raises(ValueError, match=STALE_MESSAGE):
        repository.update_state(JobStateChange(
            owner=OWNER, scope=SCOPE, job_id=ExtensionJobId(FIRST_JOB),
            expected_revision=FIRST_REVISION, state=JobState.SUCCEEDED,
        ))

    finished = repository.update_state(JobStateChange(
        owner=OWNER,
        scope=SCOPE,
        job_id=ExtensionJobId(FIRST_JOB),
        expected_revision=SECOND_REVISION,
        state=JobState.SUCCEEDED,
        result=SUCCEEDED_RESULT,
    ))
    assert (finished.state, finished.revision, finished.result) == (
        "succeeded", THIRD_REVISION, SUCCEEDED_RESULT,
    )


def test_read_returns_none_for_an_unknown_job(
    main: repository_dependencies.SqliteDatabase,
) -> None:
    """An unknown job identity has no row."""
    repository = repository_dependencies.SqliteExtensionJobRepository(main)
    assert repository.read(OWNER, SCOPE, ExtensionJobId("missing")) is None


def test_jobs_in_state_selects_one_state(
    main: repository_dependencies.SqliteDatabase,
) -> None:
    """A state read returns only the jobs in that state."""
    repository = repository_dependencies.SqliteExtensionJobRepository(main)
    repository.accept_command(command(FIRST_JOB, FIRST_KEY))
    repository.accept_command(command(SECOND_JOB, "r2"))
    repository.update_state(JobStateChange(
        owner=OWNER, scope=SCOPE, job_id=ExtensionJobId(SECOND_JOB),
        expected_revision=FIRST_REVISION, state=JobState.RUNNING,
    ))
    repository.accept_command(command("job-3", "r3"))
    repository.update_state(JobStateChange(
        owner=OWNER, scope=SCOPE, job_id=ExtensionJobId("job-3"),
        expected_revision=FIRST_REVISION, state=JobState.SUCCEEDED,
    ))

    assert [job.job_id for job in repository.jobs_in_state(JobState.ACCEPTED, 10)] == [FIRST_JOB]
    assert [job.job_id for job in repository.jobs_in_state(JobState.RUNNING, 10)] == [SECOND_JOB]
