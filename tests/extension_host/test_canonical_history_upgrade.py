# Copyright (c) 2026 Zhambyl Yermagambet
"""Retain exact core history when canonical identity gains a history revision."""

from dataclasses import replace
from pathlib import Path

import pytest

from domain import event_session, outcomes
from repository.impl.sqlite import canonical_events
from tests import sqlite_migration_events as events, sqlite_test_fixtures as core
from tests.extension_host import canonical_history_fixture as fixtures, observation_upgrade_fixture as upgrades

NEXT_CURSOR = 102


def test_history_upgrade_preserves_core_rows(tmp_path: Path) -> None:
    """Backfill original bytes, accepted order, verdicts, and links into the default history."""
    previous = fixtures.previous(tmp_path)
    before = upgrades.core_rows(previous)
    upgraded = upgrades.upgraded(previous)
    assert upgrades.core_rows(upgraded) == before
    events.require_original(upgraded)
    with upgraded.read() as connection:
        verdicts = connection.execute("SELECT history_revision, runtime_revision FROM interpretations").fetchall()
        assert tuple(connection.execute("SELECT * FROM canonical_histories").fetchone()) == ("default", None)
    assert all(tuple(row) == ("default", None) for row in verdicts)


def test_upgrade_does_not_reapply_lifecycle(tmp_path: Path) -> None:
    """Copying old facts cannot fire a new live session update."""
    previous = fixtures.previous(tmp_path)
    with previous.write() as connection:
        connection.execute("UPDATE sessions SET lifecycle='finished'")
    upgraded = upgrades.upgraded(previous)
    with upgraded.read() as connection:
        state = connection.execute("SELECT lifecycle FROM sessions").fetchone()[0]
    assert state == "finished"


@pytest.mark.parametrize("empty", [False, True])
def test_deleted_canonical_cursor_stays_reserved(tmp_path: Path, *, empty: bool) -> None:
    """Deleted high-water marks survive even when no accepted fact remains."""
    previous = fixtures.previous(tmp_path)
    with previous.write() as connection:
        connection.execute("UPDATE sqlite_sequence SET seq=101 WHERE name='canonical_events'")
        if empty:
            connection.execute("DELETE FROM canonical_events")
    upgraded = upgrades.upgraded(previous)
    cursor = fixtures.insert_core(upgraded, core.a_started_event("next"))
    assert cursor == NEXT_CURSOR


def test_empty_database_gets_default_history(tmp_path: Path) -> None:
    """An empty old store needs no canonical sequence or interpretation row."""
    upgraded = upgrades.upgraded(fixtures.previous(tmp_path, populated=False))
    assert fixtures.insert_core(upgraded, core.a_started_event(), "default") == 1
    assert canonical_events.SqliteCanonicalEventRepository(upgraded).find(core.a_started_event().event_id) is not None


def test_raw_rows_survive_history_upgrade(tmp_path: Path) -> None:
    """The canonical rebuild does not copy, delete, or change the raw input store."""
    previous = fixtures.previous(tmp_path)
    fixtures.insert_extension_raw(previous)
    with previous.read() as connection:
        before = tuple(tuple(row) for row in connection.execute("SELECT * FROM raw_events ORDER BY id"))
    upgraded = upgrades.upgraded(previous)
    with upgraded.read() as connection:
        after = tuple(tuple(row) for row in connection.execute("SELECT * FROM raw_events ORDER BY id"))
    assert after == before


def test_candidate_finish_keeps_live_session(tmp_path: Path) -> None:
    """The migrated lifecycle trigger excludes candidate facts."""
    upgraded = upgrades.upgraded(fixtures.previous(tmp_path))
    payload = event_session.SessionFinished(outcomes.Outcome.SUCCEEDED, None)
    finished = replace(core.a_started_event(), payload=payload)
    fixtures.insert_core(upgraded, finished)
    with upgraded.read() as connection:
        state = connection.execute("SELECT lifecycle FROM sessions").fetchone()[0]
    assert state == "running"
