# Copyright (c) 2026 Zhambyl Yermagambet
"""Preserve old raw bytes and source links when adding the extension branch."""

from pathlib import Path

from repository.impl.sqlite import raw_events
from tests import sqlite_migration_events as events, sqlite_test_fixtures as core
from tests.extension_host import observation_upgrade_fixture as fixtures


def test_populated_upgrade_preserves_core_history(tmp_path: Path) -> None:
    """The actual schema-29 upgrade retains every original event and link."""
    previous = fixtures.previous(tmp_path)
    before = fixtures.core_rows(previous)
    upgraded = fixtures.upgraded(previous)
    assert fixtures.core_rows(upgraded) == before
    events.require_original(upgraded)
    fixtures.require_core_metadata(upgraded)


def test_upgrade_preserves_deleted_cursor_limit(tmp_path: Path) -> None:
    """The next raw ID stays above the old AUTOINCREMENT high-water mark."""
    previous = fixtures.previous(tmp_path)
    raw_events.SqliteRawEventRepository(previous).record((core.a_raw_event("removed"),))
    with previous.write() as connection:
        removed = connection.execute("SELECT id FROM raw_events WHERE raw_event_id='removed'").fetchone()[0]
        connection.execute("DELETE FROM raw_events WHERE raw_event_id='removed'")
    upgraded = fixtures.upgraded(previous)
    raw_events.SqliteRawEventRepository(upgraded).record((core.a_raw_event("next"),))
    with upgraded.read() as connection:
        cursor = connection.execute("SELECT id FROM raw_events WHERE raw_event_id='next'").fetchone()[0]
    assert cursor == removed + 1


def test_empty_upgrade_accepts_first_core_input(tmp_path: Path) -> None:
    """An old empty raw table does not require an existing sequence row."""
    previous = fixtures.previous(tmp_path, populated=False)
    upgraded = fixtures.upgraded(previous)
    raw_events.SqliteRawEventRepository(upgraded).record((core.a_raw_event(),))
    with upgraded.read() as connection:
        cursor = connection.execute("SELECT id FROM raw_events").fetchone()[0]
    assert cursor == 1
