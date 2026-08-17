"""Plan-limit snapshots over SQLite.

Generic: the harness name is a column, so nothing here names a concrete harness
and the shared-vocabulary rule is satisfied by construction.
"""

from __future__ import annotations

from harness.models import AccountUsageSnapshot
from repository.contract.usage import AccountUsageRepository
from repository.impl.sqlite import rows
from repository.model.usage import AccountUsageWindowRow
from repository.impl.sqlite.connection import SqliteDatabase
from repository.mapper import usage as mapper


class SqliteAccountUsageRepository(AccountUsageRepository):
    def __init__(self, database: SqliteDatabase) -> None:
        self.database = database

    def record(self, snapshot: AccountUsageSnapshot) -> None:
        with self.database.write() as connection:
            connection.execute(
                "INSERT INTO account_usage_snapshots(harness, account_id, display_name, captured_at) "
                "VALUES(?, ?, ?, ?) ON CONFLICT(harness, account_id) DO UPDATE SET "
                "display_name=excluded.display_name, captured_at=excluded.captured_at",
                mapper.snapshot_values(snapshot),
            )
            connection.execute(
                "DELETE FROM account_usage_windows WHERE harness=? AND account_id=?",
                (snapshot.harness, snapshot.account_id or mapper.NO_ACCOUNT),
            )
            connection.executemany(
                "INSERT INTO account_usage_windows("
                "harness, account_id, window_key, used_percent, resets_at) VALUES(?, ?, ?, ?, ?)",
                mapper.window_values(snapshot),
            )

    def snapshots(self) -> tuple[AccountUsageSnapshot, ...]:
        with self.database.read() as connection:
            snapshot_rows = connection.execute(
                "SELECT * FROM account_usage_snapshots ORDER BY captured_at DESC"
            ).fetchall()
            window_rows = connection.execute("SELECT * FROM account_usage_windows").fetchall()
        windows: dict[tuple[str, str], list[AccountUsageWindowRow]] = {}
        for row in window_rows:
            window = rows.account_usage_window(row)
            windows.setdefault((window.harness, window.account_id), []).append(window)
        return tuple(
            mapper.account_usage_snapshot(
                snapshot := rows.account_usage_snapshot(row),
                tuple(windows.get((snapshot.harness, snapshot.account_id), ())),
            )
            for row in snapshot_rows
        )
