# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep required cleanup after the complete acceptance transaction."""

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from repository.impl.sqlite.connection import SqliteDatabase
from tests import sqlite_migration_fixture as snapshots
from tests.extension_host import lifecycle_pipeline_fixture as fixture, runtime_commit_fixture as commits
from tests.plugin_tests import translation_stage_fixture as native


def test_failed_commit_keeps_finish_pending(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed COMMIT has no reaction or memory release; a retry accepts once."""
    case = fixture.installed(tmp_path, native.claude_hook("SessionEnd"))
    core = case.core
    before = snapshots.snapshot(case.pipeline.original.store.database)
    with closing(commits.fault_connection(case.pipeline.original.store.database)) as connection:
        monkeypatch.setattr(SqliteDatabase, "_thread_connection", lambda _database: connection)
        with pytest.raises(sqlite3.OperationalError, match="injected COMMIT"):
            case.pipeline.run_batch()
        assert snapshots.snapshot(case.pipeline.original.store.database) == before
        core.reaction.react.assert_not_called()
        core.plugin.translator.release_session.assert_not_called()
        core.plugin.sources.release_session.assert_not_called()
        assert case.pipeline.run_batch() == 1
        assert case.pipeline.run_batch() == 0
        core.reaction.react.assert_called_once()
        core.plugin.translator.release_session.assert_called_once()
        core.plugin.sources.release_session.assert_called_once()


@pytest.mark.parametrize("table", ["interpretation_journals", "canonical_events", "interpretation_events"])
def test_failed_write_keeps_required_pending(tmp_path: Path, table: str) -> None:
    """No required fact or callback survives a failure inside the transaction."""
    case = fixture.installed(tmp_path, native.claude_hook("SessionEnd"))
    core = case.core
    database = case.pipeline.original.store.database
    with database.write() as connection:
        connection.execute(
            f"CREATE TRIGGER fail_lifecycle BEFORE INSERT ON {table} "
            "BEGIN SELECT RAISE(ABORT, 'injected lifecycle failure'); END",
        )
    before = snapshots.snapshot(database)
    with pytest.raises(sqlite3.IntegrityError, match="injected lifecycle"):
        case.pipeline.run_batch()
    assert snapshots.snapshot(database) == before
    core.reaction.react.assert_not_called()
    core.plugin.translator.release_session.assert_not_called()
    core.plugin.sources.release_session.assert_not_called()
