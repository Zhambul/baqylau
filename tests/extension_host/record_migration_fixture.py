# Copyright (c) 2026 Zhambyl Yermagambet
"""Seed and read an owner's stored records around a real package upgrade."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from baqylau_extension_api.models.documents import SchemaRef
from baqylau_extension_api.models.scopes import InstallationScope

from tests.extension_api import migration_samples

if TYPE_CHECKING:
    import sqlite3

    from repository.impl.sqlite.connection import SqliteDatabase

OWNER = migration_samples.OWNER
COLLECTION = migration_samples.COLLECTION
SCOPE = InstallationScope().model_dump_json()
REVISION = 5


@dataclass(frozen=True)
class StoredRow:
    """Keep the columns that a migration may change, with the unchanged revision."""

    schema_version: int
    document: str
    summary: str | None
    revision: int


def seed(database: SqliteDatabase, labels: tuple[str, ...]) -> None:
    """Store version-one records and their projection cursor as a live projector would."""
    schema_ref = migration_samples.schema(1).reference.model_dump_json()
    with database.write() as connection:
        for index, label in enumerate(labels):
            connection.execute(
                "INSERT INTO extension_records(owner, collection, record_key, scope, state, revision, "
                "projection_revision, schema_ref, document, summary) VALUES(?, ?, ?, ?, 'stored', ?, 'default', "
                "?, ?, 'Before conversion.')",
                (OWNER, COLLECTION, f"key-{index}", SCOPE, REVISION, schema_ref, f'{{"label":"{label}"}}'),
            )
        connection.execute(
            "INSERT INTO extension_projection_cursors(owner, scope, history_revision, generation, commit_cursor, "
            "updated_at) VALUES(?, ?, 'default', 'default', ?, 1.0)",
            (OWNER, SCOPE, REVISION),
        )


def live_rows(database: SqliteDatabase) -> list[StoredRow]:
    """Read the owner's live records in key order.

    Returns:
        The schema version, document, summary, and revision of each row.

    """
    with database.read() as connection:
        rows = connection.execute(
            "SELECT schema_ref, document, summary, revision FROM extension_records WHERE owner=? ORDER BY record_key",
            (OWNER,),
        ).fetchall()
    return [_row(row) for row in rows]


def generation_rows(database: SqliteDatabase, generation: str) -> list[StoredRow]:
    """Read the mirror records of one generation in key order.

    Returns:
        The schema version, document, summary, and revision of each row.

    """
    with database.read() as connection:
        rows = connection.execute(
            "SELECT schema_ref, document, summary, revision FROM extension_candidate_records "
            "WHERE generation=? ORDER BY record_key",
            (generation,),
        ).fetchall()
    return [_row(row) for row in rows]


def generations(database: SqliteDatabase) -> dict[str, str]:
    """Read every projection generation of the owner and its state.

    Returns:
        The state by generation.

    """
    with database.read() as connection:
        rows = connection.execute(
            "SELECT generation, state FROM extension_projection_generations WHERE owner=?", (OWNER,),
        ).fetchall()
    return {row["generation"]: row["state"] for row in rows}


def cursor(database: SqliteDatabase, generation: str) -> int | None:
    """Read the owner's projection cursor of one generation.

    Returns:
        The cursor, or None when the generation has none.

    """
    with database.read() as connection:
        row = connection.execute(
            "SELECT commit_cursor FROM extension_projection_cursors WHERE owner=? AND generation=?",
            (OWNER, generation),
        ).fetchone()
    return None if row is None else int(row["commit_cursor"])


def _row(row: sqlite3.Row) -> StoredRow:
    return StoredRow(
        schema_version=SchemaRef.model_validate_json(row["schema_ref"]).version,
        document=row["document"], summary=row["summary"], revision=int(row["revision"]),
    )
