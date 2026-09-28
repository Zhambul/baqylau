# Copyright (c) 2026 Zhambyl Yermagambet
"""Accept and advance durable extension jobs in one database."""

import sqlite3
import time

from baqylau_extension_api.models.scopes import ExtensionScope
from pydantic import TypeAdapter

from domain.extension_jobs import JobKind, JobState
from domain.ids import CanonicalEventId, ExtensionJobId
from repository.contract.extension_jobs import (
    CommandJobRequest,
    ExtensionJob,
    JobStateChange,
    ObserverJobRequest,
)
from repository.impl.sqlite import connection

_OPEN_JOBS_SQL = "SELECT COUNT(*) FROM extension_jobs WHERE state IN ('accepted', 'running') AND owner=?"
_SCOPE_ADAPTER: TypeAdapter[ExtensionScope] = TypeAdapter(ExtensionScope)
_SELECT_JOB_SQL = "SELECT * FROM extension_jobs WHERE owner=? AND scope=? AND job_id=?"
_SELECT_COMMAND_SQL = (
    "SELECT * FROM extension_jobs WHERE owner=? AND scope=? AND kind='command' AND request_key=?"
)
_SELECT_OBSERVER_SQL = (
    "SELECT * FROM extension_jobs WHERE owner=? AND scope=? AND kind='observer' AND cause_event_id=?"
)
_INSERT_SQL = (
    "INSERT INTO extension_jobs(owner, scope, job_id, kind, request_key, cause_event_id, state, revision, "
    "binding, request, consumer_cursor, created_at, updated_at) "
    "VALUES(?, ?, ?, ?, ?, ?, 'accepted', 1, ?, ?, ?, ?, ?)"
)
_UPDATE_SQL = (
    "UPDATE extension_jobs SET state=?, revision=revision+1, result=?, diagnostic=?, updated_at=?, "
    "binding=COALESCE(?, binding), request=COALESCE(?, request) "
    "WHERE owner=? AND scope=? AND job_id=? AND revision=?"
)
_STATE_SQL = "SELECT * FROM extension_jobs WHERE state=? ORDER BY updated_at, job_id LIMIT ?"
STALE_MESSAGE = "extension job changed since it was read"


class SqliteExtensionJobRepository:
    """Accept and advance durable extension jobs."""

    def __init__(self, database: connection.SqliteDatabase) -> None:
        """Store the database handle."""
        self.database = database

    def accept_command(self, command_job_request: CommandJobRequest) -> ExtensionJob:
        """Store one command, returning the existing job for a repeated request key.

        Returns:
            The accepted or already accepted command job.

        """
        with self.database.write() as connection_handle:
            return accept_command(connection_handle, command_job_request)

    def accept_observer(self, observer_job_request: ObserverJobRequest) -> ExtensionJob:
        """Store one observer job with its cause and consumer cursor.

        Returns:
            The accepted or already accepted observer job.

        """
        with self.database.write() as connection_handle:
            return accept_observer(connection_handle, observer_job_request)

    def read(self, owner: str, scope: ExtensionScope, job_id: ExtensionJobId) -> ExtensionJob | None:
        """Return one stored job, or None when it does not exist.

        Returns:
            The stored job, or None.

        """
        with self.database.read() as connection_handle:
            row = connection_handle.execute(
                _SELECT_JOB_SQL, (owner, scope.model_dump_json(), job_id),
            ).fetchone()
        return None if row is None else _job(row)

    def jobs_in_state(self, job_state: JobState, limit: int) -> tuple[ExtensionJob, ...]:
        """Return the oldest jobs in one state, for recovery and scheduling.

        Returns:
            At most the limit, oldest update first.

        """
        with self.database.read() as connection_handle:
            rows = connection_handle.execute(_STATE_SQL, (job_state, limit)).fetchall()
        return tuple(_job(row) for row in rows)

    def open_jobs(self, owner: str) -> int:
        """Count the owner's open jobs through the state index.

        Returns:
            The accepted and running jobs of the owner.

        """
        with self.database.read() as connection_handle:
            return int(connection_handle.execute(_OPEN_JOBS_SQL, (owner,)).fetchone()[0])

    def update_state(self, job_state_change: JobStateChange) -> ExtensionJob:
        """Advance one job when its revision still matches.

        Returns:
            The stored job after the change.

        """
        with self.database.write() as connection_handle:
            return update_state(connection_handle, job_state_change)


def accept_command(connection_handle: sqlite3.Connection, command_job_request: CommandJobRequest) -> ExtensionJob:
    """Store one command, returning the existing job for a repeated request key.

    Returns:
        The accepted or already accepted command job.

    """
    scope_text = command_job_request.scope.model_dump_json()
    existing = connection_handle.execute(
        _SELECT_COMMAND_SQL, (command_job_request.owner, scope_text, command_job_request.request_key),
    ).fetchone()
    if existing is not None:
        return _job(existing)
    now = time.time()
    connection_handle.execute(_INSERT_SQL, (
        command_job_request.owner, scope_text, command_job_request.job_id, JobKind.COMMAND,
        command_job_request.request_key, None, command_job_request.binding, command_job_request.request,
        None, now, now,
    ))
    row = connection_handle.execute(
        _SELECT_JOB_SQL, (command_job_request.owner, scope_text, command_job_request.job_id),
    ).fetchone()
    assert row is not None  # noqa: S101 -- The insert just stored this job.
    return _job(row)


def accept_observer(connection_handle: sqlite3.Connection, observer_job_request: ObserverJobRequest) -> ExtensionJob:
    """Store one observer job, returning the existing job for a repeated cause.

    Returns:
        The accepted or already accepted observer job.

    """
    scope_text = observer_job_request.scope.model_dump_json()
    existing = connection_handle.execute(
        _SELECT_OBSERVER_SQL, (observer_job_request.owner, scope_text, observer_job_request.cause_event_id),
    ).fetchone()
    if existing is not None:
        return _job(existing)
    now = time.time()
    connection_handle.execute(_INSERT_SQL, (
        observer_job_request.owner, scope_text, observer_job_request.job_id, JobKind.OBSERVER,
        None, observer_job_request.cause_event_id, observer_job_request.binding, observer_job_request.request,
        observer_job_request.consumer_cursor, now, now,
    ))
    row = connection_handle.execute(
        _SELECT_JOB_SQL, (observer_job_request.owner, scope_text, observer_job_request.job_id),
    ).fetchone()
    assert row is not None  # noqa: S101 -- The insert just stored this job.
    return _job(row)


def update_state(connection_handle: sqlite3.Connection, job_state_change: JobStateChange) -> ExtensionJob:
    """Advance one job when its revision still matches.

    Returns:
        The stored job after the change.

    Raises:
        ValueError: If the stored revision no longer matches.

    """
    scope_text = job_state_change.scope.model_dump_json()
    cursor = connection_handle.execute(_UPDATE_SQL, (
        job_state_change.state,
        job_state_change.result,
        job_state_change.diagnostic,
        time.time(),
        job_state_change.binding,
        job_state_change.request,
        job_state_change.owner,
        scope_text,
        job_state_change.job_id,
        job_state_change.expected_revision,
    ))
    if cursor.rowcount != 1:
        raise ValueError(STALE_MESSAGE)
    row = connection_handle.execute(
        _SELECT_JOB_SQL, (job_state_change.owner, scope_text, job_state_change.job_id),
    ).fetchone()
    assert row is not None  # noqa: S101 -- The update just matched the primary key.
    return _job(row)


def _job(row: sqlite3.Row) -> ExtensionJob:
    return ExtensionJob(
        owner=row["owner"],
        scope=_SCOPE_ADAPTER.validate_json(row["scope"]),
        job_id=ExtensionJobId(row["job_id"]),
        kind=JobKind(row["kind"]),
        request_key=row["request_key"],
        cause_event_id=None if row["cause_event_id"] is None else CanonicalEventId(row["cause_event_id"]),
        state=JobState(row["state"]),
        revision=int(row["revision"]),
        binding=row["binding"],
        request=row["request"],
        result=row["result"],
        diagnostic=row["diagnostic"],
        consumer_cursor=None if row["consumer_cursor"] is None else int(row["consumer_cursor"]),
    )
