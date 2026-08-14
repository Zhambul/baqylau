"""Plugin-owned current rate-limit snapshots captured from Claude Code."""

from __future__ import annotations

import json
import os
import sqlite3
import time
from contextlib import closing

from plugins.claude_code import application_data


def _database_path() -> str:
    return application_data.path("usage.db")


def _connect() -> sqlite3.Connection:
    path = _database_path()
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS usage_snapshots(
            session_id TEXT PRIMARY KEY,
            account_id TEXT,
            display_name TEXT NOT NULL,
            windows TEXT NOT NULL,
            captured_at REAL NOT NULL
        )
        """
    )
    return connection


def record(
    session_id: str,
    account_id: str | None,
    display_name: str,
    windows: dict,
    captured_at: float | None = None,
) -> None:
    with closing(_connect()) as connection, connection:
        connection.execute(
            "INSERT OR REPLACE INTO usage_snapshots VALUES(?,?,?,?,?)",
            (
                session_id,
                account_id,
                display_name,
                json.dumps(windows, separators=(",", ":"), sort_keys=True),
                captured_at or time.time(),
            ),
        )


def latest_by_account() -> dict[str | None, dict]:
    with closing(_connect()) as connection, connection:
        rows = connection.execute(
            "SELECT * FROM usage_snapshots ORDER BY captured_at DESC"
        ).fetchall()
    snapshots = {}
    for row in rows:
        account_id = row["account_id"] or None
        if account_id not in snapshots:
            snapshots[account_id] = {
                "display_name": row["display_name"],
                "windows": json.loads(row["windows"]),
                "captured_at": row["captured_at"],
            }
    return snapshots
