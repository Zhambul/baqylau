# Copyright (c) 2026 Zhambyl Yermagambet
"""Read closed lifecycle models inside one caller-owned SQLite transaction."""

import sqlite3

from baqylau_extension_api.models.base import Identifier
from pydantic import TypeAdapter

from extensions.models.lifecycle_operations import (
    LifecycleFailure,
    LifecycleOperation,
    LifecycleProposal,
    OperationStatus,
)
from extensions.models.lifecycle_selection import ExtensionIntent, OwnerSettings, RuntimeSelection
from extensions.models.lifecycle_state import LifecycleState
from extensions.models.runtime_candidates import RuntimeCandidate
from extensions.models.runtime_resolution import RuntimeResolution
from extensions.models.settings import SettingsOverrides
from repository.impl.sqlite.extension_shutdown import latest_record


def read_state(connection: sqlite3.Connection) -> LifecycleState:
    """Read all current rows at the same SQLite snapshot.

    Returns:
        Stored intent and committed selection, not observed process liveness.

    """
    head = connection.execute("SELECT * FROM extension_lifecycle_head WHERE id=1").fetchone()
    if head is None:
        return LifecycleState()
    return LifecycleState(
        revision=int(head["revision"]), manager_id=str(head["manager_id"]),
        committed_runtime=_committed_runtime(connection, optional_text(head, "committed_runtime")),
        pending_operation=optional_text(head, "pending_operation"),
        last_shutdown=latest_record(connection),
        intents=tuple(ExtensionIntent(
            extension_id=str(row["extension_id"]), enabled=bool(row["enabled"]),
            package_digest=optional_text(row, "package_digest"),
        ) for row in connection.execute("SELECT * FROM extension_requests ORDER BY extension_id")),
        settings=tuple(OwnerSettings(
            extension_id=str(row["extension_id"]),
            settings=SettingsOverrides.model_validate_json(str(row["overrides"])),
        ) for row in connection.execute("SELECT * FROM extension_settings ORDER BY extension_id")),
    )


def read_runtime(connection: sqlite3.Connection, runtime_revision: str | None) -> RuntimeCandidate | None:
    """Read the exact retained selection without checking mutable package files.

    Returns:
        A reserved runtime candidate, whether or not it later became committed.

    """
    row = connection.execute(
        "SELECT selection, resolution FROM extension_runtime_revisions "
        "LEFT JOIN extension_runtime_resolutions USING(runtime_revision) WHERE runtime_revision=?", (runtime_revision,),
    ).fetchone()
    if row is None:
        return None
    resolution = _resolution(row)
    return (TypeAdapter(RuntimeCandidate).validate_json(str(row["selection"]))
            if resolution is None else resolution.runtime)


def read_operation(connection: sqlite3.Connection, operation_id: Identifier | None) -> LifecycleOperation | None:
    """Restore an operation through its full typed outcome validation.

    Returns:
        The current operation or no row for an unknown request.

    """
    row = connection.execute(
        "SELECT operations.*, resolutions.resolution FROM extension_lifecycle_operations AS operations "
        "LEFT JOIN extension_runtime_resolutions AS resolutions USING(runtime_revision) WHERE operation_id=?",
        (operation_id,),
    ).fetchone()
    if row is None:
        return None
    return LifecycleOperation(
        proposal=LifecycleProposal.model_validate_json(str(row["proposal"])),
        accepted_revision=int(row["accepted_revision"]),
        status=TypeAdapter[OperationStatus](OperationStatus).validate_python(str(row["status"])),
        created_at=float(row["created_at"]), updated_at=float(row["updated_at"]),
        failure=_failure(row), resolution=_resolution(row),
    )


def optional_text(row: sqlite3.Row, name: str) -> str | None:
    """Keep nullable stored fields distinct from the string representation of None.

    Returns:
        Text or the original absent value.

    """
    return None if row[name] is None else str(row[name])


def _failure(row: sqlite3.Row) -> LifecycleFailure | None:
    encoded = optional_text(row, "failure")
    return None if encoded is None else LifecycleFailure.model_validate_json(encoded)


def _resolution(row: sqlite3.Row) -> RuntimeResolution | None:
    encoded = optional_text(row, "resolution")
    return None if encoded is None else RuntimeResolution.model_validate_json(encoded)


def _committed_runtime(connection: sqlite3.Connection, runtime_revision: str | None) -> RuntimeSelection | None:
    selected = read_runtime(connection, runtime_revision)
    if selected is not None and not isinstance(selected, RuntimeSelection):
        message = "committed extension runtime has unresolved settings"
        raise ValueError(message)
    return selected
