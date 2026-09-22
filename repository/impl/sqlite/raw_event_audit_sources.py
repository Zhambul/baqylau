# Copyright (c) 2026 Zhambyl Yermagambet
"""Detect the journal sources a forensic database provides."""

import sqlite3
from dataclasses import dataclass

LOOKUP_SOURCES = ("interpretation_journals", "interpretation_steps", "interpretation_journal_steps")
STEP_SOURCES = ("interpretation_steps", "interpretation_journal_steps")


@dataclass(frozen=True)
class StepSource:
    """Name one stored step source and the codec version it holds."""

    name: str
    codec: int


def available_sources(connection: sqlite3.Connection) -> tuple[str, ...]:
    """Read which journal sources this database has.

    Returns:
        The present source names in their fixed order.

    """
    placeholders = ", ".join("?" for _ in LOOKUP_SOURCES)
    rows = connection.execute(
        f"SELECT name FROM sqlite_master WHERE name IN ({placeholders})",  # noqa: S608 -- Fixed source names.
        LOOKUP_SOURCES,
    ).fetchall()
    names = {str(row[0]) for row in rows}
    return tuple(source for source in LOOKUP_SOURCES if source in names)


def step_sources(sources: tuple[str, ...]) -> tuple[StepSource, ...]:
    """Select the step sources that this database has.

    Returns:
        The present step sources with their codec versions.

    """
    return tuple(
        StepSource(name=source, codec=1 if source == "interpretation_steps" else 2)
        for source in STEP_SOURCES if source in sources
    )


def has_columns(connection: sqlite3.Connection, table: str, names: tuple[str, ...]) -> bool:
    """Check whether one table or view has every named column.

    Returns:
        True when every named column is present.

    """
    rows = connection.execute("SELECT name FROM pragma_table_info(?)", (table,)).fetchall()
    present = {str(row[0]) for row in rows}
    return set(names) <= present
