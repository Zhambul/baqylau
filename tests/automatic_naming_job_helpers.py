# Copyright (c) 2026 Zhambyl Yermagambet
"""Durable jobs, title safety, and generic naming semantics."""

import typing

from domain import naming as naming_models
from naming.audit import NamingAudit
from naming.jobs import NamingJobWorker
from repository.impl.sqlite.naming import SqliteNamingJobRepository
from tests.automatic_naming_models_one import Audit, FixedModels, RawEvents, Sessions
from tests.automatic_naming_namer_helper import namer
from tests.automatic_naming_session_helper import session
from tests.sqlite_schema_fixture import baseline_database

if typing.TYPE_CHECKING:
    from audit.recorder import AuditRecorder
    from repository.contract.sessions import SessionRepository


def unavailable_naming_worker(
    jobs: SqliteNamingJobRepository,
    audit: Audit,
) -> NamingJobWorker:
    """Build a worker whose model reports that it is unavailable.

    Returns:
        The worker with the supplied job store and audit recorder.

    """
    return NamingJobWorker(
        jobs,
        typing.cast("SessionRepository", Sessions(session())),
        namer(FixedModels(unavailable=True), jobs, RawEvents(), audit=audit),
        typing.cast("AuditRecorder", audit),
    )


def assert_job_enqueued_once(
    repository: SqliteNamingJobRepository,
    job: naming_models.NamingJob,
) -> None:
    """Verify that a naming job is accepted only once."""
    accepted = repository.enqueue(job)
    assert accepted
    assert not repository.enqueue(job)


def assert_failed_naming_job(
    jobs: SqliteNamingJobRepository,
    job_key: str,
    audit: Audit,
    status: str,
) -> None:
    """Check the stored failure and its audit state, with no application error."""
    stored = jobs.find(job_key)
    assert stored is not None
    assert stored.state == naming_models.NamingJobState.FAILED
    assert not audit.errors
    state = typing.cast("tuple[object, ...]", audit.states[-1])
    assert state[-1] == NamingAudit(job_key=job_key, status=status)


def remove_naming_schema(database_path: str) -> None:
    """Remove the naming table and set version 13 for the migration test."""
    database = baseline_database(database_path)
    database.initialize()
    with database.write() as connection:
        connection.execute("DROP TABLE naming_jobs")
        connection.execute("UPDATE schema_version SET version=13 WHERE id=1")
