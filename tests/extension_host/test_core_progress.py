# Copyright (c) 2026 Zhambyl Yermagambet
"""Advance the core checkpoint only through verified live extension facts."""

from pathlib import Path

import pytest

from repository.impl.sqlite.session_data import SqliteSessionDataRepository
from tests import sqlite_migration_fixture as snapshots
from tests.extension_host import interpretation_snapshot_fixture as storage, reaction_fixture as fixture

EXTENSION_COUNT = 3
PAST_SQL_CURSOR = 9_223_372_036_854_775_808


def test_skip_has_no_display_change(tmp_path: Path) -> None:
    """An extension-only prefix needs no session, entry, or display wake."""
    case = fixture.installed(tmp_path, core=False)
    fixture.append_extensions(case, EXTENSION_COUNT)
    with case.loop.dependencies.changes.subscribe_thread() as changed:
        case.view.advance_past_extensions(EXTENSION_COUNT)
        assert not changed.is_set()
    assert case.view.progress() == EXTENSION_COUNT
    assert case.view.high_water_cursor() == 0
    assert not case.view.visible()
    assert len(case.store.current_fact_page(0, 10).facts) == EXTENSION_COUNT


def test_skip_retry_does_not_write_or_rewind(tmp_path: Path) -> None:
    """An exact or older extension skip leaves the whole database unchanged."""
    case = fixture.installed(tmp_path, core=False)
    fixture.append_extensions(case, EXTENSION_COUNT)
    case.view.advance_past_extensions(EXTENSION_COUNT)
    database = case.view.sqlite_database
    before = snapshots.snapshot(database)
    case.view.advance_past_extensions(EXTENSION_COUNT)
    case.view.advance_past_extensions(1)
    assert snapshots.snapshot(database) == before


def test_skip_survives_new_repository(tmp_path: Path) -> None:
    """A fresh reader resumes from the stored core checkpoint."""
    case = fixture.installed(tmp_path, core=False)
    fixture.append_extensions(case)
    case.view.advance_past_extensions(1)
    restarted = SqliteSessionDataRepository(storage.repository(tmp_path).database)
    assert restarted.progress() == 1
    assert restarted.high_water_cursor() == 0


@pytest.mark.parametrize("cursor", [0, -1, True, PAST_SQL_CURSOR])
def test_invalid_skip_cursor_is_rejected(tmp_path: Path, cursor: int) -> None:
    """Only strict positive SQLite cursor values can name a skip."""
    case = fixture.installed(tmp_path, core=False)
    fixture.append_extensions(case)
    before = snapshots.snapshot(case.view.sqlite_database)
    with pytest.raises(ValueError, match="validation error"):
        case.view.advance_past_extensions(cursor)
    assert snapshots.snapshot(case.view.sqlite_database) == before


@pytest.mark.parametrize("cursor", [1, 1000])
def test_core_or_missing_target_is_rejected(tmp_path: Path, cursor: int) -> None:
    """The public skip method cannot act as an unchecked progress setter."""
    case = fixture.installed(tmp_path)
    before = snapshots.snapshot(case.view.sqlite_database)
    with pytest.raises(ValueError, match="recorded live extension fact"):
        case.view.advance_past_extensions(cursor)
    assert snapshots.snapshot(case.view.sqlite_database) == before


def test_unpublished_target_is_rejected(tmp_path: Path) -> None:
    """A candidate-only cursor cannot move the live consumer."""
    case = fixture.installed(tmp_path, core=False)
    storage.seed(case.store, (storage.fact("candidate"),), "candidate")
    with pytest.raises(ValueError, match="recorded live extension fact"):
        case.view.advance_past_extensions(1)
    assert case.view.progress() == 0


def test_skip_cannot_cross_unprocessed_core(tmp_path: Path) -> None:
    """A valid extension target cannot hide earlier failed or pending core work."""
    case = fixture.installed(tmp_path)
    fixture.append_extensions(case)
    before = snapshots.snapshot(case.view.sqlite_database)
    with pytest.raises(ValueError, match="cannot skip unprocessed core"):
        case.view.advance_past_extensions(EXTENSION_COUNT)
    assert snapshots.snapshot(case.view.sqlite_database) == before
