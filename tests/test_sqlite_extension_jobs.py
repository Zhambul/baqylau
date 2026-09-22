# Copyright (c) 2026 Zhambyl Yermagambet
"""Accept and advance durable extension jobs."""

from __future__ import annotations

import pytest
from baqylau_extension_api.models import scopes

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


def command(job_id: str, request_key: str) -> CommandJobRequest:
    """Build one command request.

    Returns:
        The command request.

    """
    return CommandJobRequest(
        owner=OWNER, scope=SCOPE, job_id=job_id, request_key=request_key, binding=BINDING, request=REQUEST,
    )


def observer(job_id: str, cause_event_id: str) -> ObserverJobRequest:
    """Build one observer request.

    Returns:
        The observer request.

    """
    return ObserverJobRequest(
        owner=OWNER,
        scope=SCOPE,
        job_id=job_id,
        cause_event_id=cause_event_id,
        binding=BINDING,
        request=REQUEST,
        consumer_cursor=CONSUMER_CURSOR,
    )


def test_command_accept_deduplicates_by_request_key(
    main: repository_dependencies.SqliteDatabase,
) -> None:
    """A repeated command request key returns the first accepted job."""
    repository = repository_dependencies.SqliteExtensionJobRepository(main)
    first = repository.accept_command(command("job-1", "r1"))
    repeated = repository.accept_command(command("job-2", "r1"))

    assert first.job_id == "job-1"
    assert first.kind == "command"
    assert first.state == "accepted"
    assert first.revision == FIRST_REVISION
    assert repeated.job_id == "job-1"


def test_observer_accept_deduplicates_by_cause(
    main: repository_dependencies.SqliteDatabase,
) -> None:
    """A repeated observer cause returns the first job and keeps its cursor."""
    repository = repository_dependencies.SqliteExtensionJobRepository(main)
    first = repository.accept_observer(observer("job-1", "event-1"))
    repeated = repository.accept_observer(observer("job-2", "event-1"))

    assert first.kind == "observer"
    assert first.cause_event_id == "event-1"
    assert first.consumer_cursor == CONSUMER_CURSOR
    assert repeated.job_id == "job-1"


def test_update_state_advances_and_rejects_a_stale_revision(
    main: repository_dependencies.SqliteDatabase,
) -> None:
    """A state change uses the exact revision and rejects an old one."""
    repository = repository_dependencies.SqliteExtensionJobRepository(main)
    repository.accept_command(command("job-1", "r1"))

    running = repository.update_state(JobStateChange(
        owner=OWNER, scope=SCOPE, job_id="job-1", expected_revision=FIRST_REVISION, state="running",
    ))
    assert (running.state, running.revision) == ("running", SECOND_REVISION)

    with pytest.raises(ValueError, match=STALE_MESSAGE):
        repository.update_state(JobStateChange(
            owner=OWNER, scope=SCOPE, job_id="job-1", expected_revision=FIRST_REVISION, state="succeeded",
        ))

    finished = repository.update_state(JobStateChange(
        owner=OWNER,
        scope=SCOPE,
        job_id="job-1",
        expected_revision=SECOND_REVISION,
        state="succeeded",
        result='{"status":"succeeded"}',
    ))
    assert (finished.state, finished.revision, finished.result) == (
        "succeeded", THIRD_REVISION, '{"status":"succeeded"}',
    )


def test_read_returns_none_for_an_unknown_job(
    main: repository_dependencies.SqliteDatabase,
) -> None:
    """An unknown job identity has no row."""
    repository = repository_dependencies.SqliteExtensionJobRepository(main)
    assert repository.read(OWNER, SCOPE, "missing") is None
