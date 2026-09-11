# Copyright (c) 2026 Zhambyl Yermagambet
"""Check the goal-dismissal schema upgrade without removing session data."""

from pathlib import Path

from repository.contract.session_data import SessionDataChanges
from repository.impl.sqlite.databases import main_database
from repository.impl.sqlite.goal_dismissals import SqliteGoalDismissalRepository
from repository.impl.sqlite.session_data import SqliteSessionDataRepository
from tests import canonical_sessiondata_api_values as fixture


def test_goal_dismissal_upgrade_keeps_sessions(tmp_path: Path) -> None:
    """Upgrade a version 24 file and retain its session."""
    database = main_database(str(tmp_path / "main.db"))
    SqliteSessionDataRepository(database).apply(fixture.SESSION, SessionDataChanges(session=fixture.FACTS), 1)
    with database.write() as connection:
        connection.execute("DROP TABLE goal_dismissals")
        connection.execute("UPDATE schema_version SET version=24 WHERE id=1")
    upgraded = main_database(database.path)
    upgraded.initialize()
    assert not SqliteGoalDismissalRepository(upgraded).hidden(fixture.SESSION)
    restored = SqliteSessionDataRepository(upgraded).read(fixture.SESSION)
    assert restored is not None
    assert restored.session == fixture.FACTS
