# Copyright (c) 2026 Zhambyl Yermagambet
"""The feed is ordered by the fact that each row belongs to, then by the row.

A projection rebuild wrote a session's adapters rows again when it became live,
and a delayed projector writes rows after the rows of later facts. Both showed
at the end of the feed, because a page was ordered by the row alone.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from tests import (
    sqlite_domain_dependencies as domain_dependencies,
    sqlite_repository_dependencies as repository_dependencies,
    sqlite_test_dependencies as test_dependencies,
    sqlite_test_migrations,
)

if TYPE_CHECKING:
    from domain.entries import SessionEntry

SESSION = domain_dependencies.domain_ids.SessionId("session-one")
FACTS = (("first", 10), ("second", 20), ("third", 30))
LATE_FACT = 20
LATE_COMMIT = 40
FEED = ("first", "second", "late", "third")


def test_a_late_row_pages_at_its_fact(main: repository_dependencies.SqliteDatabase) -> None:
    """A row written after later facts is in its fact's place in a page and in older pages."""
    store = _feed_with_a_late_row(main)

    whole = store.entries_page(SESSION, limit=10).entries
    newest = store.entries_page(SESSION, limit=2)
    older = store.entries_page(SESSION, before=newest.oldest_cursor, limit=10).entries

    assert tuple(entry.entry_id for entry in whole) == FEED
    paged = (*older, *newest.entries)
    assert tuple(entry.entry_id for entry in paged) == FEED


def test_a_page_at_a_cursor_has_its_facts(main: repository_dependencies.SqliteDatabase) -> None:
    """`at` is a canonical cursor: a page at it has every row of the facts at or before it."""
    store = _feed_with_a_late_row(main)

    page = store.entries_page(SESSION, at=LATE_FACT, limit=10).entries

    assert [entry.entry_id for entry in page] == ["first", "second", "late"]


def _feed_with_a_late_row(
    main: repository_dependencies.SqliteDatabase,
) -> test_dependencies.SqliteSessionDataRepository:
    store = test_dependencies.SqliteSessionDataRepository(main)
    for entry_id, cursor in FACTS:
        store.apply(SESSION, _changes(sqlite_test_migrations.an_entry(entry_id)), cursor)
    late = replace(sqlite_test_migrations.an_entry("late"), commit_cursor=LATE_FACT)
    store.apply(SESSION, _changes(late), LATE_COMMIT)
    return store


def _changes(entry: SessionEntry) -> repository_dependencies.SessionDataChanges:
    return repository_dependencies.SessionDataChanges(entries=(entry,))
