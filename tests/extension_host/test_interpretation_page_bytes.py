# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep an ordered mixed page inside its content budget, or return one large fact."""

from pathlib import Path
from unittest.mock import Mock

import pytest

from repository.impl.sqlite import interpretation_codec, interpretation_pages
from tests.extension_host import interpretation_page_fixture as fixture, interpretation_snapshot_fixture as storage

ENCODED_TEXT = ('"plain"', '"é📘"', r'"quote: \" slash: \\"')
LARGE_TEXT = 8192
SCOPED_FIELD = "scoped"
FIRST = "first"
SECOND = "second"


@pytest.mark.parametrize(SCOPED_FIELD, [False, True])
@pytest.mark.parametrize("encoded", ENCODED_TEXT)
def test_exact_content_budget_keeps_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, encoded: str, *, scoped: bool,
) -> None:
    """Count stored UTF-8 text, including metadata and escaped document content."""
    store = storage.repository(tmp_path)
    storage.seed(store, (
        storage.fact(FIRST, encoded=encoded), storage.fact(SECOND, encoded=encoded),
    ))
    budget = sum(fixture.content_sizes(store))
    monkeypatch.setattr(interpretation_pages, fixture.BUDGET_FIELD, budget)
    complete = fixture.read(store, scoped=scoped)
    assert fixture.identities(complete) == (FIRST, SECOND)
    monkeypatch.setattr(interpretation_pages, fixture.BUDGET_FIELD, budget - 1)
    short = fixture.read(store, scoped=scoped)
    assert fixture.identities(short) == (FIRST,)
    assert short.head == complete.head


@pytest.mark.parametrize(SCOPED_FIELD, [False, True])
@pytest.mark.parametrize("limit", [1, 2])
def test_count_bound_remains_in_effect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, limit: int, *, scoped: bool,
) -> None:
    """A large byte budget does not expand the requested row count."""
    store = storage.repository(tmp_path)
    storage.seed(store, (
        storage.fact(FIRST), storage.fact(SECOND), storage.fact("third"),
    ))
    monkeypatch.setattr(interpretation_pages, fixture.BUDGET_FIELD, sum(fixture.content_sizes(store)))
    page = fixture.read(store, limit=limit, scoped=scoped)
    assert len(page.facts) == limit
    assert page.facts[-1].cursor < page.head
    assert not fixture.read(store, page.head, scoped=scoped).facts


@pytest.mark.parametrize(SCOPED_FIELD, [False, True])
def test_large_middle_fact_does_not_hide_tail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, scoped: bool,
) -> None:
    """A later small fact cannot pass a large fact; the next page returns it alone."""
    store = storage.repository(tmp_path)
    encoded = '"{text}"'.format(text="x" * LARGE_TEXT)
    storage.seed(store, (
        storage.fact(FIRST), storage.fact("large", encoded=encoded), storage.fact("last"),
    ))
    monkeypatch.setattr(interpretation_pages, fixture.BUDGET_FIELD, fixture.SMALL_BUDGET)
    first = fixture.read(store, scoped=scoped)
    large = fixture.read(store, first.facts[-1].cursor, scoped=scoped)
    assert fixture.identities(first) == (FIRST,)
    assert fixture.identities(large) == ("large",)
    last = fixture.read(store, large.facts[-1].cursor, scoped=scoped)
    assert fixture.identities(last) == ("last",)


@pytest.mark.parametrize(SCOPED_FIELD, [False, True])
def test_excluded_bodies_are_not_decoded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, scoped: bool,
) -> None:
    """The SQL page returns only the selected prefix to the Python body decoder."""
    store = storage.repository(tmp_path)
    storage.seed(store, (storage.fact(FIRST), storage.fact(SECOND)))
    monkeypatch.setattr(interpretation_pages, fixture.BUDGET_FIELD, fixture.content_sizes(store)[0])
    decoder = Mock(wraps=interpretation_codec.stored_fact)
    monkeypatch.setattr(interpretation_codec, "stored_fact", decoder)
    assert fixture.identities(fixture.read(store, scoped=scoped)) == (FIRST,)
    decoder.assert_called_once()
