"""Application-owned activity-pane width preferences."""

from __future__ import annotations

import os
import sqlite3
from contextlib import closing

from core.data import data_directory

DEFAULT_WIDTH_PERCENT = 25
DEFAULT_RESIZE_COLUMNS = 4


def _configured_integer(name: str, default: int) -> int:
    configured = os.environ.get(name)
    return default if not configured else int(configured)


def configured_width_percent() -> int:
    width = _configured_integer(
        "BAQYLAU_ACTIVITY_WIDTH_PERCENT",
        DEFAULT_WIDTH_PERCENT,
    )
    if not 1 <= width <= 99:
        raise ValueError("activity pane width must be between 1 and 99 percent")
    return width


def resize_columns() -> int:
    columns = _configured_integer(
        "BAQYLAU_ACTIVITY_RESIZE_COLUMNS",
        DEFAULT_RESIZE_COLUMNS,
    )
    if columns <= 0:
        raise ValueError("activity pane resize step must be positive")
    return columns


def _database_path() -> str:
    return os.path.join(data_directory(), "pane-preferences.db")


def width_percent(working_directory: str) -> int:
    database_path = _database_path()
    if not os.path.isfile(database_path):
        return configured_width_percent()
    with closing(
        sqlite3.connect(f"file:{database_path}?mode=ro", uri=True, timeout=0.2)
    ) as connection:
        row = connection.execute(
            "SELECT width_percent FROM project_widths WHERE working_directory=?",
            (os.path.realpath(working_directory),),
        ).fetchone()
    return row[0] if row is not None else configured_width_percent()


def remember_width(working_directory: str, width: int) -> None:
    if not 1 <= width <= 99:
        raise ValueError("activity pane width must be between 1 and 99 percent")
    database_path = _database_path()
    os.makedirs(os.path.dirname(database_path), exist_ok=True)
    with closing(sqlite3.connect(database_path, timeout=0.2)) as connection, connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS project_widths("
            "working_directory TEXT PRIMARY KEY, width_percent INTEGER NOT NULL)"
        )
        connection.execute(
            "INSERT INTO project_widths(working_directory, width_percent) VALUES(?, ?) "
            "ON CONFLICT(working_directory) DO UPDATE SET width_percent=excluded.width_percent",
            (os.path.realpath(working_directory), width),
        )
