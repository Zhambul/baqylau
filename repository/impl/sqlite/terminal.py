"""Pane widths and opened content views over SQLite."""

from __future__ import annotations

from repository.contract.terminal import ContentViewRepository, PaneWidthRepository
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


class SqliteContentViewRepository(ContentViewRepository):
    def __init__(self, database: SqliteDatabase) -> None:
        self.database = database

    def opened(self) -> frozenset[str]:
        with self.database.read() as connection:
            found = connection.execute("SELECT content_reference FROM opened_views").fetchall()
        return frozenset(row["content_reference"] for row in found)

    def toggle(self, content_reference: str, toggled_at: float) -> bool:
        if not content_reference:
            raise ValueError("content reference is required")
        with self.database.write() as connection:
            existing = connection.execute(
                "SELECT 1 FROM opened_views WHERE content_reference=?", (content_reference,)
            ).fetchone()
            if existing is None:
                connection.execute(
                    "INSERT INTO opened_views(content_reference, opened_at) VALUES(?, ?)",
                    (content_reference, toggled_at),
                )
                return True
            connection.execute(
                "DELETE FROM opened_views WHERE content_reference=?", (content_reference,)
            )
        return False
