# Copyright (c) 2026 Zhambyl Yermagambet
"""One commit can add several feed entries without losing or repeating any."""

from __future__ import annotations

from tests import (
    sqlite_domain_dependencies as domain_dependencies,
    sqlite_repository_dependencies as repository_dependencies,
    sqlite_test_dependencies as test_dependencies,
    sqlite_test_migrations,
)

SESSION = domain_dependencies.domain_ids.SessionId("session-one")
MULTI_ENTRY_CURSOR = 20
MULTI_ENTRY_COUNT = 3
MULTI_ENTRY_IDS = ("multi-1", "multi-2", "multi-3")


def apply_multi_entry_commit(store: test_dependencies.SqliteSessionDataRepository) -> None:
    """Commit three entries at one canonical boundary."""
    entries = tuple(
        sqlite_test_migrations.an_entry(f"multi-{ordinal}") for ordinal in range(1, MULTI_ENTRY_COUNT + 1)
    )
    store.apply(SESSION, repository_dependencies.SessionDataChanges(entries=entries), MULTI_ENTRY_CURSOR)


def test_one_commit_adds_ordered_entries(main: repository_dependencies.SqliteDatabase) -> None:
    """One commit can add several entries; the delta returns them once, in order."""
    store = test_dependencies.SqliteSessionDataRepository(main)
    apply_multi_entry_commit(store)

    delta = store.delta(SESSION, MULTI_ENTRY_CURSOR - 1)
    delta_ids = tuple(entry.entry_id for entry in delta.entries)
    assert (delta_ids, delta.cursor) == (MULTI_ENTRY_IDS, MULTI_ENTRY_CURSOR)
    assert store.delta(SESSION, delta.cursor).empty


def test_multi_entry_commit_pages_without_loss(main: repository_dependencies.SqliteDatabase) -> None:
    """Paging across a multi-entry commit returns every entry exactly once."""
    store = test_dependencies.SqliteSessionDataRepository(main)
    apply_multi_entry_commit(store)

    newest = store.entries_page(SESSION, limit=2)
    older = store.entries_page(SESSION, before=newest.oldest_cursor, limit=2)
    older_ids = [entry.entry_id for entry in older.entries]
    assert [entry.entry_id for entry in newest.entries] == ["multi-2", "multi-3"]
    assert (older_ids, older.has_more) == (["multi-1"], False)
