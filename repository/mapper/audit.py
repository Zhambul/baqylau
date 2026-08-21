"""Row DTOs to operational records, and back.

Absorbs the write side's JSON coercion and its content truncation — both used to
sit inside the free functions that also opened the database.
"""

from __future__ import annotations

import json

from pydantic import JsonValue

from audit.models import (
    ApplicationError,
    ApplicationErrorRecord,
    SpawnRecord,
    StateFileRecord,
    StreamOpened,
)
from repository.model.audit import ErrorRow
from repository.model.sql import SqlValues

# A audit context is arbitrary caller data. It is recorded, never queried,
# so it is bounded rather than shaped. `JsonValue` (not `object`) is the real
# shape: every caller passes something that is ABOUT to become the JSON this
# writes, and `json.dumps` below rejects anything that is not.
CONTENT_LIMIT = 2000


def text(value: JsonValue) -> str:
    """Any caller value as one string. Never raises: this runs inside `except`."""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, default=str)


def truncated(value: JsonValue) -> str:
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
