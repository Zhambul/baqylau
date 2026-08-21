"""Pane widths and opened content views over SQLite."""

from __future__ import annotations

from repository.contract.terminal import PaneWidthRepository
from repository.impl.sqlite.connection import SqliteDatabase


class SqlitePaneWidthRepository(PaneWidthRepository):
    def __init__(self, database: SqliteDatabase) -> None:
        self.database = database

    def width_percent(self, working_directory: str) -> int | None:
        with self.database.read() as connection:
            row = connection.execute(
                "SELECT width_percent FROM pane_widths WHERE working_directory=?",
                (working_directory,),
            ).fetchone()
        return int(row["width_percent"]) if row is not None else None

    def remember_width(self, working_directory: str, width_percent: int) -> None:
        with self.database.write() as connection:
            connection.execute(
                "INSERT INTO pane_widths(working_directory, width_percent) VALUES(?, ?) "
                "ON CONFLICT(working_directory) DO UPDATE SET width_percent=excluded.width_percent",
                (working_directory, width_percent),
            )
