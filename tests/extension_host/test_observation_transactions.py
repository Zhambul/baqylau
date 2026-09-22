# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep original bytes and pending input atomic through real write failures."""

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from repository.impl.sqlite.connection import SqliteDatabase
from tests import sqlite_migration_fixture as snapshots
from tests.extension_host import (
    observation_fixture as fixtures,
    observation_requests as requests,
    runtime_commit_fixture as commits,
)

REPEATED_TEXT = "é " * 1000
UNICODE_DOCUMENT = f'\n  "{REPEATED_TEXT}"\r\n'


def test_failed_queue_insert_restores_raw_store(tmp_path: Path) -> None:
    """Failure after a raw insert rolls back its bytes, sequence, and pending row."""
    case = fixtures.installed(tmp_path)
    with case.store.database.write() as connection:
        connection.execute(
            "CREATE TRIGGER fail_pending BEFORE INSERT ON pending_raw_events "
            "BEGIN SELECT RAISE(ABORT, 'injected pending failure'); END",
        )
    before = snapshots.snapshot(case.store.database)
    with pytest.raises(sqlite3.IntegrityError, match="injected pending"):
        case.store.append_observations(case.request)
    assert snapshots.snapshot(case.store.database) == before


def test_failed_commit_retains_no_original_input(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A real connection with an injected COMMIT failure can retry the same request."""
    case = fixtures.installed(tmp_path)
    before = snapshots.snapshot(case.store.database)
    with closing(commits.fault_connection(case.store.database)) as connection:
        monkeypatch.setattr(SqliteDatabase, "_thread_connection", lambda _database: connection)
        with pytest.raises(sqlite3.OperationalError, match="injected COMMIT"):
            case.store.append_observations(case.request)
        assert snapshots.snapshot(case.store.database) == before
        accepted = case.store.append_observations(case.request).accepted
        assert case.store.pending_observations(10) == accepted


def test_compression_preserves_exact_utf8_bytes(tmp_path: Path) -> None:
    """Stored compression does not normalize JSON spacing, Unicode, or line endings."""
    case = fixtures.installed(tmp_path)
    request = requests.document(case.request, UNICODE_DOCUMENT)
    stored = case.store.append_observations(request).accepted[0]
    assert fixtures.original(stored).candidate.document.json_text == UNICODE_DOCUMENT
    with case.store.database.read() as connection:
        row = connection.execute("SELECT payload, payload_codec FROM raw_events").fetchone()
    assert row["payload_codec"] == "zlib"
    assert len(row["payload"]) < len(UNICODE_DOCUMENT.encode("utf-8"))


def test_earlier_original_can_be_a_parent(tmp_path: Path) -> None:
    """A new observation can retain a stable reference to an earlier original."""
    case = fixtures.installed(tmp_path)
    parent = case.store.append_observations(case.request).accepted[0]
    request = requests.new_key(case.request, "child")
    request = requests.causes(request, (parent.observation.raw_event_id,))
    child = case.store.append_observations(request).accepted[0]
    assert fixtures.original(child).candidate.causes == (parent.observation.raw_event_id,)
