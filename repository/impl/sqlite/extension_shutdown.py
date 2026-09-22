# Copyright (c) 2026 Zhambyl Yermagambet
"""Retain shutdown observations without changing job outcomes or lifecycle intent."""

import sqlite3

from extensions.models.cleanup import ShutdownRecord


def append_record(connection: sqlite3.Connection, record: ShutdownRecord) -> bool:
    """Retain an observation from a known manager, including one which was fenced.

    Returns:
        True for a stored observation or an exact retry; false for an unknown manager.

    Raises:
        ValueError: If an existing identity has a different body.

    """
    known = connection.execute(
        "SELECT EXISTS(SELECT 1 FROM extension_lifecycle_head WHERE manager_id=?) "
        "OR EXISTS(SELECT 1 FROM extension_lifecycle_operations WHERE manager_id=?)",
        (record.manager_id, record.manager_id),
    ).fetchone()
    if not bool(known[0]):
        return False
    existing = connection.execute(
        "SELECT observation FROM extension_shutdown_records WHERE record_id=?", (record.record_id,),
    ).fetchone()
    if existing is not None:
        if ShutdownRecord.model_validate_json(str(existing["observation"])) != record:
            message = "shutdown observation identity has a different body"
            raise ValueError(message)
        return True
    connection.execute(
        "INSERT INTO extension_shutdown_records(record_id, manager_id, recorded_at, observation) VALUES(?, ?, ?, ?)",
        (record.record_id, record.manager_id, record.recorded_at, record.model_dump_json()),
    )
    return True


def latest_record(connection: sqlite3.Connection) -> ShutdownRecord | None:
    """Read the latest observation, including one from a prior manager.

    Returns:
        The stored result; no observation is proof of job recovery.

    """
    row = connection.execute(
        "SELECT observation FROM extension_shutdown_records ORDER BY cursor DESC LIMIT 1",
    ).fetchone()
    return None if row is None else ShutdownRecord.model_validate_json(str(row["observation"]))
