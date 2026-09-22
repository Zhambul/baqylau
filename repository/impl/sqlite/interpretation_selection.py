# Copyright (c) 2026 Zhambyl Yermagambet
"""Fence complete interpretations against original input and committed runtime state."""

import sqlite3

from extensions.models.interpretations import InterpretationBinding
from extensions.models.lifecycle_selection import RuntimeSelection
from extensions.models.observations import StoredObservation
from extensions.models.processing_input import observation_scope
from repository.impl.sqlite import observation_codec, processing_runtime


def selected_runtime(connection: sqlite3.Connection, binding: InterpretationBinding) -> RuntimeSelection:
    """Require the current manager generation and one complete committed runtime.

    Returns:
        The immutable package and settings selection.

    """
    return processing_runtime.require_runtime(connection, binding.manager_id, binding.runtime_revision)


def original_input(connection: sqlite3.Connection, binding: InterpretationBinding) -> StoredObservation:
    """Bind the proposal to the stored raw cursor, identity, and complete scope.

    Returns:
        The actual immutable original observation.

    Raises:
        ValueError: If the original is missing or its selected identity differs.

    """
    row = connection.execute("SELECT * FROM raw_events WHERE raw_event_id=?", (binding.raw_event_id,)).fetchone()
    if row is None:
        message = "interpretation original input is not recorded"
        raise ValueError(message)
    original = observation_codec.stored_observation(row)
    if original.cursor != binding.input_cursor or observation_scope(original) != binding.scope:
        message = "interpretation input cursor or scope does not match its original"
        raise ValueError(message)
    return original


def require_history(connection: sqlite3.Connection, binding: InterpretationBinding) -> None:
    """Keep replay out of the live history and reject an unknown history.

    Raises:
        ValueError: If the history or processing mode is not valid.

    """
    if (binding.mode == "live") != (binding.history_revision == "default"):
        message = "interpretation mode does not match the selected history"
        raise ValueError(message)
    history = connection.execute(
        "SELECT 1 FROM canonical_histories WHERE history_revision=?", (binding.history_revision,),
    ).fetchone()
    if history is None:
        message = "interpretation history is not registered"
        raise ValueError(message)


def canonical_head(connection: sqlite3.Connection, history_revision: str) -> int:
    """Read the accepted boundary for one explicit history.

    Returns:
        Its last accepted cursor, or zero before its first fact.

    """
    row = connection.execute(
        "SELECT COALESCE(MAX(cursor), 0) FROM canonical_events WHERE history_revision=?", (history_revision,),
    ).fetchone()
    return int(row[0])
