"""Row DTOs to operational records, and back.

Absorbs the write side's JSON coercion and its content truncation — both used to
sit inside the free functions that also opened the database.
"""

from __future__ import annotations

import json

from audit.models import (
    ApplicationError,
    ApplicationErrorRecord,
    SpawnRecord,
    StateFileRecord,
    StreamOpened,
)
from repository.model.audit import ErrorRow

# A audit context is arbitrary caller data. It is recorded, never queried,
# so it is bounded rather than shaped.
CONTENT_LIMIT = 2000


def text(value: object) -> str:
    """Any caller value as one string. Never raises: this runs inside `except`."""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, default=str)


def truncated(value: object) -> str:
    return text(value)[:CONTENT_LIMIT]


def application_error(row: ErrorRow) -> ApplicationError:
    return ApplicationError(
        error_id=int(row.id),
        timestamp=float(row.ts),
        component=row.script or "",
        action=row.func or "",
        traceback=row.traceback or "",
        context=row.context or "",
    )


def error_values(record: ApplicationErrorRecord) -> tuple[object, ...]:
    return (
        record.timestamp,
        record.session_id,
        record.script,
        record.function,
        record.traceback,
        record.context,
        record.process_id,
    )


def state_file_values(record: StateFileRecord) -> tuple[object, ...]:
    return (
        record.timestamp,
        record.session_id,
        record.path,
        record.action,
        record.content,
        record.script,
        record.process_id,
    )


def spawn_values(record: SpawnRecord) -> tuple[object, ...]:
    return (
        record.timestamp,
        record.session_id,
        record.parent_script,
        record.child_process_id,
        record.argv,
        record.purpose,
    )


def stream_values(record: StreamOpened) -> tuple[object, ...]:
    return (
        record.session_id,
        record.kind,
        record.agent_id,
        record.task_id,
        record.source_path,
        record.process_id,
        record.started_at,
    )
