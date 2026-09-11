# Copyright (c) 2026 Zhambyl Yermagambet
"""Check database commit notices and output expiry."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import Mock

import pytest

from core.change_signal import ChangeSignal
from core.work_queue import WorkKind, WorkQueue
from domain import ids, shells, work_state
from repository.impl.sqlite.databases import main_database
from repository.impl.sqlite.shell_output import SqliteShellOutputRepository
from tests.harness_names import CODEX_HARNESS

if TYPE_CHECKING:
    from pathlib import Path

    from repository.impl.sqlite.connection import SqliteDatabase

FIRST_OUTPUT_CREATED_AT = 1.0
SECOND_OUTPUT_CREATED_AT = 2.0
FIRST_OUTPUT_EXPIRES_AT = 1.5
SECOND_OUTPUT_EXPIRES_AT = 2.5


def test_only_commit_wakes_worker(tmp_path: Path) -> None:
    """Publish only after a successful transaction with stored changes."""
    database = main_database(str(tmp_path / "main.db"))
    notices = Mock(spec=WorkQueue)
    database.work_queue = notices
    changes = Mock(spec=ChangeSignal)
    database.changes = changes

    with pytest.raises(ValueError, match="rollback"):
        _rollback_write(database)
    notices.put.assert_not_called()
    changes.publish.assert_not_called()
    with database.write(WorkKind.RAW) as connection:
        connection.execute("UPDATE schema_version SET applied_at=456 WHERE id=1")
        notices.put.assert_not_called()
    notices.put.assert_called_once_with(WorkKind.RAW)
    changes.publish.assert_called_once_with()


def _rollback_write(database: SqliteDatabase) -> None:
    with database.write(WorkKind.RAW) as connection:
        connection.execute("UPDATE schema_version SET applied_at=123 WHERE id=1")
        message = "rollback"
        raise ValueError(message)


def test_output_expires_without_active_session(tmp_path: Path) -> None:
    """Find the next lifetime deadline after a restart with no active sessions."""
    database = main_database(str(tmp_path / "main.db"))
    outputs = SqliteShellOutputRepository(database)
    assert outputs.oldest_created_at() is None
    for created_at in (FIRST_OUTPUT_CREATED_AT, SECOND_OUTPUT_CREATED_AT):
        outputs.save(
            shells.ShellOutputFollowing(
                session_id=ids.SessionId("closed"),
                shell_id=ids.ShellId(str(created_at)),
                harness=CODEX_HARNESS,
                actor_id=ids.ActorId("lead"),
                parent_actor_id=None,
                source_path=str(tmp_path / str(created_at)),
                chunk_source_type="output",
                delete_source=False,
                initial_size=0,
                initial_modified_at=0,
                wait_for_source_change=False,
                until=work_state.ShellFollowUntil.SESSION_FINISHED,
                state=shells.ShellFollowState.ACTIVE,
                created_at=created_at,
            ),
        )
    assert outputs.oldest_created_at() == pytest.approx(FIRST_OUTPUT_CREATED_AT)
    outputs.remove_expired(FIRST_OUTPUT_EXPIRES_AT)
    assert outputs.oldest_created_at() == pytest.approx(SECOND_OUTPUT_CREATED_AT)
    outputs.remove_expired(SECOND_OUTPUT_EXPIRES_AT)
    assert outputs.oldest_created_at() is None


def test_internal_commit_keeps_readers_asleep(tmp_path: Path) -> None:
    """Keep engine notices without making streams read unchanged data."""
    database = main_database(str(tmp_path / "main.db"))
    queue = Mock(spec=WorkQueue)
    changes = Mock(spec=ChangeSignal)
    database.work_queue = queue
    database.changes = changes
    with database.write(WorkKind.RAW, notify_readers=False) as connection:
        connection.execute("UPDATE schema_version SET applied_at=789 WHERE id=1")
    queue.put.assert_called_once_with(WorkKind.RAW)
    changes.publish.assert_not_called()
