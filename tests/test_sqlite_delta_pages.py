# Copyright (c) 2026 Zhambyl Yermagambet
"""A delta larger than its content budget comes in pages of complete commits, without loss."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from repository.impl.sqlite import session_entry_rows
from tests import (
    sqlite_domain_dependencies as domain_dependencies,
    sqlite_repository_dependencies as repository_dependencies,
    sqlite_test_dependencies as test_dependencies,
    sqlite_test_migrations,
)

if TYPE_CHECKING:
    from repository.contract.session_data import SessionDelta

SESSION = domain_dependencies.domain_ids.SessionId("session-one")
FIRST_CURSOR = 20
FIRST_IDS = ("a-1", "a-2")
SECOND_IDS = ("b-1",)
LAST_IDS = ("c-1", "c-2")


@pytest.fixture
def paged_store(
    main: repository_dependencies.SqliteDatabase, monkeypatch: pytest.MonkeyPatch,
) -> test_dependencies.SqliteSessionDataRepository:
    """Store three commits, and make each commit larger than the content budget.

    Returns:
        The session data store.

    """
    store = test_dependencies.SqliteSessionDataRepository(main)
    for offset, entry_ids in enumerate((FIRST_IDS, SECOND_IDS, LAST_IDS)):
        _commit(store, FIRST_CURSOR + offset, entry_ids)
    monkeypatch.setattr(session_entry_rows, "PAGE_CONTENT_BYTES", 1)
    return store


def _commit(store: test_dependencies.SqliteSessionDataRepository, cursor: int, entry_ids: tuple[str, ...]) -> None:
    entries = tuple(sqlite_test_migrations.an_entry(entry_id) for entry_id in entry_ids)
    store.apply(SESSION, repository_dependencies.SessionDataChanges(entries=entries), cursor)


def _next(store: test_dependencies.SqliteSessionDataRepository, delta: SessionDelta) -> SessionDelta:
    return store.delta(SESSION, delta.cursor, delta.entry_cursor)


def _page(delta: SessionDelta) -> tuple[tuple[str, ...], bool]:
    return tuple(entry.entry_id for entry in delta.entries), delta.complete


def test_pages_hold_complete_commits(paged_store: test_dependencies.SqliteSessionDataRepository) -> None:
    """Each page holds complete commits, and the pages together hold every entry once."""
    first = paged_store.delta(SESSION, FIRST_CURSOR - 1)
    second = _next(paged_store, first)
    last = _next(paged_store, second)

    assert _page(first) == (FIRST_IDS, False)
    assert _page(second) == (SECOND_IDS, False)
    assert _page(last) == (LAST_IDS, True)
    assert _next(paged_store, last).empty


def test_reconnect_at_page_cursor_reads_on(paged_store: test_dependencies.SqliteSessionDataRepository) -> None:
    """A reader that names only the cursor of a stopped page gets the next commit."""
    first = paged_store.delta(SESSION, FIRST_CURSOR - 1)

    assert _page(paged_store.delta(SESSION, first.cursor)) == (SECOND_IDS, False)


def test_late_entries_stay_in_one_page(
    main: repository_dependencies.SqliteDatabase, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An entry committed after the reader read its commit is never cut from its page."""
    store = test_dependencies.SqliteSessionDataRepository(main)
    _commit(store, FIRST_CURSOR, ("core",))
    read = store.delta(SESSION, FIRST_CURSOR - 1)
    _commit(store, FIRST_CURSOR, FIRST_IDS)
    _commit(store, FIRST_CURSOR + 1, SECOND_IDS)
    monkeypatch.setattr(session_entry_rows, "PAGE_CONTENT_BYTES", 1)

    late = _next(store, read)
    rest = _next(store, late)

    assert _page(late) == (FIRST_IDS, False)
    assert _page(rest) == (SECOND_IDS, True)
