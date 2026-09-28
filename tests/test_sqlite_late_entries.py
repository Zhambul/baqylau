# Copyright (c) 2026 Zhambyl Yermagambet
"""A reader that follows entry rows gets an entry committed later at an old canonical cursor (P08-T03)."""

from __future__ import annotations

from tests import (
    sqlite_domain_dependencies as domain_dependencies,
    sqlite_repository_dependencies as repository_dependencies,
    sqlite_test_dependencies as test_dependencies,
    sqlite_test_migrations,
)

SESSION = domain_dependencies.domain_ids.SessionId("session-one")
FACT_CURSOR = 20
PAGE_LIMIT = 10


def commit(store: test_dependencies.SqliteSessionDataRepository, entry_id: str) -> None:
    """Commit one entry at the fact's canonical cursor, as the core consumer and a projection both do."""
    changes = repository_dependencies.SessionDataChanges(entries=(sqlite_test_migrations.an_entry(entry_id),))
    store.apply(SESSION, changes, FACT_CURSOR)


def test_late_entry_follows_the_entry_rows(main: repository_dependencies.SqliteDatabase) -> None:
    """After the core entry, a projection's entry at the same canonical cursor comes by its new row."""
    store = test_dependencies.SqliteSessionDataRepository(main)
    commit(store, "core")
    first = store.delta(SESSION, FACT_CURSOR - 1)
    commit(store, "card")

    late = store.delta(SESSION, first.cursor, first.entry_cursor)

    assert [entry.entry_id for entry in first.entries] == ["core"]
    assert [entry.entry_id for entry in late.entries] == ["card"]
    assert store.delta(SESSION, late.cursor, late.entry_cursor).empty


def test_reader_has_the_rows_of_its_cursor(main: repository_dependencies.SqliteDatabase) -> None:
    """A reader that names only its canonical cursor has every row at or before it."""
    store = test_dependencies.SqliteSessionDataRepository(main)
    commit(store, "core")
    commit(store, "card")

    delta = store.delta(SESSION, FACT_CURSOR)

    assert delta.empty
    stored = store.entries_page(SESSION, limit=PAGE_LIMIT).entries
    assert delta.entry_cursor == max(entry.cursor for entry in stored)
