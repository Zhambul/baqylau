# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep core checkpoint changes atomic at both statement and COMMIT failures."""

import sqlite3
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock

import pytest

from tests import sqlite_migration_fixture as snapshots
from tests.extension_host import reaction_fixture as fixture, runtime_commit_fixture as commits

CHECKPOINT = 2


@pytest.mark.parametrize("operation", ["INSERT", "UPDATE"])
def test_failed_checkpoint_statement_rolls_back(tmp_path: Path, operation: str) -> None:
    """A failed insert or update preserves the full logical database."""
    case = fixture.installed(tmp_path, core=False)
    fixture.append_extensions(case, CHECKPOINT)
    if operation == "UPDATE":
        case.view.advance_past_extensions(1)
    with case.view.sqlite_database.write() as connection:
        connection.execute(
            f"CREATE TRIGGER fail_progress AFTER {operation} ON reaction_progress "
            "BEGIN SELECT RAISE(ABORT, 'injected progress failure'); END",
        )
    before = snapshots.snapshot(case.view.sqlite_database)
    with pytest.raises(sqlite3.IntegrityError, match="injected progress"):
        case.view.advance_past_extensions(CHECKPOINT)
    assert snapshots.snapshot(case.view.sqlite_database) == before


def test_failed_checkpoint_commit_permits_retry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A real connection failure at COMMIT leaves no checkpoint or display notice."""
    case = fixture.installed(tmp_path, core=False)
    fixture.append_extensions(case)
    before = snapshots.snapshot(case.view.sqlite_database)
    with closing(commits.fault_connection(case.view.sqlite_database)) as connection:
        monkeypatch.setattr(case.view.sqlite_database, "_thread_connection", Mock(return_value=connection))
        with pytest.raises(sqlite3.OperationalError, match="injected COMMIT"):
            case.view.advance_past_extensions(1)
        assert snapshots.snapshot(case.view.sqlite_database) == before
        case.view.advance_past_extensions(1)
        assert case.view.progress() == 1
