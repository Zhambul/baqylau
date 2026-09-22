# Copyright (c) 2026 Zhambyl Yermagambet
"""Accept and advance durable extension jobs in one database."""

import sqlite3
import time
from typing import cast

from baqylau_extension_api.models.scopes import ExtensionScope
from pydantic import TypeAdapter

from repository.contract.extension_jobs import (
    CommandJobRequest,
    ExtensionJob,
    JobKind,
    JobState,
    JobStateChange,
    ObserverJobRequest,
)
from repository.impl.sqlite import connection

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
    "UPDATE extension_jobs SET state=?, revision=revision+1, result=?, diagnostic=?, updated_at=? "
    "WHERE owner=? AND scope=? AND job_id=? AND revision=?"
)
STALE_MESSAGE = "extension job changed since it was read"


class SqliteExtensionJobRepository:
    """Accept and advance durable extension jobs."""

    def __init__(self, database: connection.SqliteDatabase) -> None:
        """Store the database handle."""
        self.database = database

    def accept_command(self, job: CommandJobRequest) -> ExtensionJob:
        """Store one command, returning the existing job for a repeated request key.

        Returns:
            The accepted or already accepted command job.

        """
        return self._accept(job, "command")

    def accept_observer(self, job: ObserverJobRequest) -> ExtensionJob:
        """Store one observer job with its cause and consumer cursor.

        Returns:
            The accepted or already accepted observer job.

        """
        return self._accept(job, "observer")

    def read(self, owner: str, scope: ExtensionScope, job_id: str) -> ExtensionJob | None:
        """Return one stored job, or None when it does not exist.

        Returns:
            The stored job, or None.

        """
        with self.database.read() as connection_handle:
            row = connection_handle.execute(
                _SELECT_JOB_SQL, (owner, scope.model_dump_json(), job_id),
            ).fetchone()
        return None if row is None else _job(row)

    def update_state(self, change: JobStateChange) -> ExtensionJob:
        """Advance one job when its revision still matches.

        Returns:
            The stored job after the change.

        Raises:
            ValueError: If the stored revision no longer matches.

        """
        scope_text = change.scope.model_dump_json()
        with self.database.write() as connection_handle:
            cursor = connection_handle.execute(_UPDATE_SQL, (
                change.state,
                change.result,
                change.diagnostic,
                time.time(),
                change.owner,
                scope_text,
                change.job_id,
                change.expected_revision,
            ))
            if cursor.rowcount != 1:
                raise ValueError(STALE_MESSAGE)
            row = connection_handle.execute(
                _SELECT_JOB_SQL, (change.owner, scope_text, change.job_id),
            ).fetchone()
        assert row is not None  # noqa: S101 -- The update just matched the primary key.
        return _job(row)

    def _accept(self, job: CommandJobRequest | ObserverJobRequest, kind: JobKind) -> ExtensionJob:
        request_key = job.request_key if isinstance(job, CommandJobRequest) else None
        cause_event_id = job.cause_event_id if isinstance(job, ObserverJobRequest) else None
        consumer_cursor = job.consumer_cursor if isinstance(job, ObserverJobRequest) else None
        scope_text = job.scope.model_dump_json()
        with self.database.write() as connection_handle:
            identity = (kind, request_key, cause_event_id)
            existing = _existing(connection_handle, job.owner, scope_text, identity)
            if existing is not None:
                return _job(existing)
            now = time.time()
            connection_handle.execute(_INSERT_SQL, (
                job.owner, scope_text, job.job_id, kind, request_key, cause_event_id,
                job.binding, job.request, consumer_cursor, now, now,
            ))
            row = connection_handle.execute(_SELECT_JOB_SQL, (job.owner, scope_text, job.job_id)).fetchone()
        assert row is not None  # noqa: S101 -- The insert just stored this job.
        return _job(row)


def _existing(
    connection_handle: sqlite3.Connection,
    owner: str,
    scope_text: str,
    identity: tuple[JobKind, str | None, str | None],
) -> sqlite3.Row | None:
    kind, request_key, cause_event_id = identity
    if kind == "command":
        row = connection_handle.execute(_SELECT_COMMAND_SQL, (owner, scope_text, request_key)).fetchone()
    else:
        row = connection_handle.execute(_SELECT_OBSERVER_SQL, (owner, scope_text, cause_event_id)).fetchone()
    return cast("sqlite3.Row | None", row)


def _job(row: sqlite3.Row) -> ExtensionJob:
    return ExtensionJob(
        owner=row["owner"],
        scope=_SCOPE_ADAPTER.validate_json(row["scope"]),
        job_id=row["job_id"],
        kind=cast("JobKind", row["kind"]),
        request_key=row["request_key"],
        cause_event_id=row["cause_event_id"],
        state=cast("JobState", row["state"]),
        revision=int(row["revision"]),
        binding=row["binding"],
        request=row["request"],
        result=row["result"],
        diagnostic=row["diagnostic"],
        consumer_cursor=None if row["consumer_cursor"] is None else int(row["consumer_cursor"]),
    )
