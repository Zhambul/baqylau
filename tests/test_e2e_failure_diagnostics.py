"""Tests for focused live E2E failure reports."""

from __future__ import annotations

import sqlite3

from tests.e2e.testkit.failure_diagnostics import _stored_window_ids


def test_stored_window_ids_include_only_windows_owned_by_the_e2e_database(tmp_path) -> None:
    database = tmp_path / "main.db"
    connection = sqlite3.connect(database)
    connection.executescript(
        """
        CREATE TABLE sessions(terminal_window_id TEXT);
        CREATE TABLE raw_events(terminal_window_id TEXT);
        INSERT INTO sessions VALUES ('e2e-host'), (NULL);
        INSERT INTO raw_events VALUES ('e2e-host'), ('e2e-child'), (NULL);
        """
    )
    connection.close()

    assert _stored_window_ids(tmp_path) == frozenset({"e2e-host", "e2e-child"})
