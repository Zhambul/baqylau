"""Row DTOs to operational records, and back.

Absorbs the write side's JSON coercion and its content truncation — both used to
sit inside the free functions that also opened the database.
"""

from __future__ import annotations

from pydantic import BaseModel

from audit.models import (
    AuditContent,
    ApplicationError,
    ApplicationErrorRecord,
    SpawnRecord,
    StateFileRecord,
    StreamOpened,
)
from repository.model.audit import ErrorRow
from repository.model.sql import SqlValues

# An audit context is recorded but is not queried. Callers still declare its
# exact shape before this boundary serializes it.
CONTENT_LIMIT = 2000


def text(value: AuditContent) -> str:
    """Any caller value as one string. Never raises: this runs inside `except`."""
    if isinstance(value, BaseModel):
        return value.model_dump_json()
    return value or ""


def truncated(value: AuditContent) -> str:
    return text(value)[:CONTENT_LIMIT]


def application_error(error_row: ErrorRow) -> ApplicationError:
    return ApplicationError(
        error_id=int(error_row.id),
        timestamp=float(error_row.ts),
        component=error_row.script or "",
        action=error_row.func or "",
        traceback=error_row.traceback or "",
        context=error_row.context or "",
    )


def error_values(application_error_record: ApplicationErrorRecord) -> SqlValues:
    return (
        application_error_record.timestamp,
        application_error_record.session_id,
        application_error_record.script,
        application_error_record.function,
        application_error_record.traceback,
        application_error_record.context,
        application_error_record.process_id,
    )


def state_file_values(state_file_record: StateFileRecord) -> SqlValues:
    return (
        state_file_record.timestamp,
        state_file_record.session_id,
        state_file_record.path,
        state_file_record.action,
        state_file_record.content,
        state_file_record.script,
        state_file_record.process_id,
    )


def spawn_values(spawn_record: SpawnRecord) -> SqlValues:
    return (
        spawn_record.timestamp,
        spawn_record.session_id,
        spawn_record.parent_script,
        spawn_record.child_process_id,
        spawn_record.argv,
        spawn_record.purpose,
    )


def stream_values(stream_opened: StreamOpened) -> SqlValues:
    return (
        stream_opened.session_id,
        stream_opened.kind,
        stream_opened.agent_id,
        stream_opened.task_id,
        stream_opened.source_path,
        stream_opened.process_id,
        stream_opened.started_at,
    )
