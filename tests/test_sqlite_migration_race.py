# Copyright (c) 2026 Zhambyl Yermagambet
"""Read the schema version only after acquiring the migration write lock."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from tests import sqlite_migration_barrier as barriers, sqlite_migration_fixture as fixtures


@pytest.mark.parametrize("fail_first", [False, True])
def test_concurrent_initializers_check_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, fail_first: bool,
) -> None:
    """The waiting initializer skips committed work or retries rolled-back work."""
    case = barriers.race(tmp_path, monkeypatch, fail_first=fail_first)
    before = fixtures.snapshot(case.original)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(case.first.initialize)
        with case.barrier.release_after():
            assert case.barrier.paused.wait(5)
            second = executor.submit(case.second.initialize)
            assert case.barrier.attempted.wait(5) and not second.done()
            assert fixtures.snapshot(case.original) == before
        if fail_first:
            with pytest.raises(sqlite3.OperationalError, match="missing_migration_table"):
                first.result(timeout=5)
        else:
            first.result(timeout=5)
        second.result(timeout=5)
    fixtures.require_copied(case.second)
