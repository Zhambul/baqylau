"""The two database handles, built from the one owner of their paths.

Constructed here rather than at each call site so that `initialize()` runs once
per file per process — four separate store objects used to apply the same schema
to the same file four times during startup.
"""

from __future__ import annotations

from core import data
from repository.impl.sqlite.connection import (
    AUDIT_PRAGMAS,
    READ_ONLY_PRAGMAS,
    SqliteDatabase,
)
from repository.impl.sqlite.schema import (
    AUDIT_SCHEMA,
    AUDIT_SCHEMA_VERSION,
    MAIN_SCHEMA,
    MAIN_MIGRATIONS,
    MAIN_SCHEMA_VERSION,
)


def main_database(path: str | None = None) -> SqliteDatabase:
    return SqliteDatabase(
        path or data.main_database_path(),
        MAIN_SCHEMA,
        MAIN_SCHEMA_VERSION,
        migrations=MAIN_MIGRATIONS,
    )


def audit_database(path: str | None = None) -> SqliteDatabase:
    return SqliteDatabase(
        path or data.audit_database_path(), AUDIT_SCHEMA, AUDIT_SCHEMA_VERSION, AUDIT_PRAGMAS
    )


def read_only(sqlite_database: SqliteDatabase) -> SqliteDatabase:
    """The same file, opened so it cannot be created, migrated or written.

    What the forensic CLI gets: the tool you run when the store is the suspect
    must not be able to alter it.
    """
    return SqliteDatabase(
        sqlite_database.path,
        sqlite_database.schema,
        sqlite_database.schema_version,
        READ_ONLY_PRAGMAS,
    )
