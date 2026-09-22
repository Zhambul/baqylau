# Copyright (c) 2026 Zhambyl Yermagambet
"""Validate original observations against the selected retained package."""

import sqlite3

from baqylau_extension_api.operations.observations import validate_observations

from extensions.models.observations import ObservationAppend
from repository.impl.sqlite import processing_runtime


def validate_append(connection: sqlite3.Connection, request: ObservationAppend) -> None:
    """Reject late workers, inactive owners, undeclared sources, and invalid documents."""
    selected = processing_runtime.require_runtime(connection, request.manager_id, request.runtime_revision)
    package = processing_runtime.require_package(connection, selected, request.extension_id)
    validate_observations(
        package.manifest, package.schemas, request.scope,
        tuple(positioned.observation for positioned in request.observations),
    )


def validate_causes(connection: sqlite3.Connection, causes: tuple[str, ...]) -> None:
    """Require a stored parent without inventing a session for its child.

    Raises:
        ValueError: If a cause does not name an already recorded input or fact.

    """
    for cause in causes:
        found = connection.execute(
            "SELECT 1 FROM raw_events WHERE raw_event_id=? "
            "UNION ALL SELECT 1 FROM current_canonical_events WHERE event_id=? LIMIT 1", (cause, cause),
        ).fetchone()
        if found is None:
            message = "observation cause is not recorded"
            raise ValueError(message)
