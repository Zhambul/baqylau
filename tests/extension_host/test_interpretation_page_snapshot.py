# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep byte-limited pages in one SQL snapshot with indexed metadata selection."""

from pathlib import Path

import pytest

from repository.impl.sqlite import interpretation_pages, interpretation_selection
from tests.extension_host import interpretation_page_fixture as fixture, interpretation_snapshot_fixture as storage

INITIAL_HEAD = 2


@pytest.mark.parametrize("scoped", [False, True])
def test_page_head_keeps_the_body_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, scoped: bool,
) -> None:
    """A commit between body and head reads is visible only in the next page."""
    store = storage.repository(tmp_path)
    storage.seed(store, (storage.fact("first"), storage.fact("second")))
    writer = fixture.ConcurrentPageWriter(storage.repository(tmp_path), interpretation_selection.canonical_head)
    writer.store.database.initialize()
    monkeypatch.setattr(interpretation_pages, fixture.BUDGET_FIELD, fixture.content_sizes(store)[0])
    monkeypatch.setattr(interpretation_selection, "canonical_head", writer)
    page = fixture.read(store, scoped=scoped)
    assert writer.written
    assert fixture.identities(page) == ("first",)
    assert page.head == INITIAL_HEAD
    following = fixture.read(store, page.facts[-1].cursor, scoped=scoped)
    assert fixture.identities(following) == ("second",)
    assert following.head == page.head + 1


@pytest.mark.parametrize(("scoped", "index"), [
    (False, "index_canonical_history_cursor"),
    (True, "index_canonical_history_scope"),
])
def test_page_metadata_uses_history_index(tmp_path: Path, index: str, *, scoped: bool) -> None:
    """Limit metadata through the exact history or scoped cursor index."""
    store = storage.repository(tmp_path)
    storage.seed(store, (storage.fact("first"),))
    statements: list[str] = []
    with store.database.read() as connection:
        connection.set_trace_callback(statements.append)
    fixture.read(store, scoped=scoped)
    with store.database.read() as connection:
        connection.set_trace_callback(None)
        query = next(statement for statement in statements if statement.startswith("WITH page_metadata"))
        plan = connection.execute(f"EXPLAIN QUERY PLAN {query}").fetchall()
    assert any(index in str(row["detail"]) for row in plan)
