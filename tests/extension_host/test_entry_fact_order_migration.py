# Copyright (c) 2026 Zhambyl Yermagambet
"""Schema 42 moves each stored extension row to the fact that caused it."""

from pathlib import Path

from repository.impl.sqlite import schema
from tests import canonical_sessiondata_fixtures as payloads
from tests.extension_host import reaction_fixture as reactions

WRONG_CURSOR = 999
EXTENSION_ROW = (
    "INSERT INTO session_entries(commit_cursor, position, entry_id, session_id, entry_type, actor_id, occurred_at, "
    "payload) VALUES(?, 0, 'card', ?, 'extension', 'actor', 1.0, json_object('source_event_id', ?))"
)
FACT_SQL = "SELECT event_id, session_id, cursor FROM canonical_events WHERE session_id IS NOT NULL ORDER BY cursor"


def test_an_extension_row_moves_to_its_fact(tmp_path: Path) -> None:
    """A row stored with a batch's last cursor takes the cursor of its source fact."""
    case = reactions.installed(tmp_path, core=False)
    reactions.append_core(case, payloads.started())
    with case.store.database.write() as connection:
        fact = connection.execute(FACT_SQL).fetchone()
        connection.execute(EXTENSION_ROW, (WRONG_CURSOR, fact["session_id"], fact["event_id"]))
        for statement in schema.MAIN_MIGRATIONS[schema.MAIN_SCHEMA_VERSION]:
            connection.execute(statement)
        moved = connection.execute("SELECT commit_cursor FROM session_entries WHERE entry_id='card'").fetchone()

    assert moved["commit_cursor"] == fact["cursor"]
