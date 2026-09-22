# Copyright (c) 2026 Zhambyl Yermagambet
"""Provide application database handles and diagnostics storage."""

from typing import Annotated

from fastapi import Depends

from app.injection import singleton
from app.provider_notifications import ApplicationUpdates
from app.provider_work_queue import EngineWork
from core import data
from repository.contract import diagnostics as diagnostics_contract, facts as facts_contract
from repository.impl.sqlite import (
    connection,
    databases,
    diagnostics as sqlite_diagnostics,
    raw_event_audits as sqlite_raw_event_audits,
)


@singleton
def main_db(work_queue: EngineWork, updates: ApplicationUpdates) -> connection.SqliteDatabase:
    """Return the main application database handle.

    Returns:
        Main application database handle.

    """
    database = databases.main_database(data.main_database_path())
    database.work_queue = work_queue
    database.changes = updates.changes
    return database


MainDb = Annotated[connection.SqliteDatabase, Depends(main_db)]


@singleton
def audit_db() -> connection.SqliteDatabase:
    """Return the operational audit database handle.

    Returns:
        Operational audit database handle.

    """
    return databases.audit_database(data.audit_database_path())


AuditDb = Annotated[connection.SqliteDatabase, Depends(audit_db)]


@singleton
def audit_reader_db(database: AuditDb) -> connection.SqliteDatabase:
    """Return a read-only handle for the operational audit database.

    Returns:
        A read-only handle for the operational audit database.

    """
    return databases.read_only(database)


AuditReaderDb = Annotated[connection.SqliteDatabase, Depends(audit_reader_db)]


@singleton
def diagnostics(
    database: MainDb,
    audit_database: AuditReaderDb,
) -> diagnostics_contract.DiagnosticsRepository:
    """Return the combined diagnostics repository.

    Returns:
        Combined diagnostics repository.

    """
    return sqlite_diagnostics.SqliteDiagnosticsRepository(
        database,
        audit_database,
    )


Diagnostics = Annotated[
    diagnostics_contract.DiagnosticsRepository,
    Depends(diagnostics),
]


@singleton
def raw_event_audits(database: MainDb) -> facts_contract.RawEventAuditRepository:
    """Return the raw-event audit repository over a read-only handle.

    Returns:
        Raw-event audit repository.

    """
    return sqlite_raw_event_audits.SqliteRawEventAuditRepository(databases.read_only(database))


RawEventAudits = Annotated[
    facts_contract.RawEventAuditRepository,
    Depends(raw_event_audits),
]
