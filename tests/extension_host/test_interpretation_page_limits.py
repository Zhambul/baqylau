# Copyright (c) 2026 Zhambyl Yermagambet
"""Exercise real default limits, scoped isolation, and core progress through small pages."""

from pathlib import Path

import pytest
from baqylau_extension_api.models import documents, scopes

from repository.impl.sqlite import interpretation_pages
from tests import canonical_sessiondata_fixtures as payloads
from tests.extension_host import (
    interpretation_page_fixture as fixture,
    interpretation_snapshot_fixture as storage,
    reaction_fixture as reactions,
)

LARGE_COUNT = 5
DEFAULT_PAGE_COUNT = 3
LAST = "last"


def test_default_budget_reduces_large_page(tmp_path: Path) -> None:
    """Several valid large documents do not all enter one count-bounded read."""
    store = storage.repository(tmp_path)
    encoded = '"{text}"'.format(text="x" * (documents.MAX_DOCUMENT_CHARACTERS - 2))
    identities = (f"large-{index}" for index in range(LARGE_COUNT))
    storage.seed(store, tuple(
        storage.fact(identity, encoded=encoded) for identity in identities
    ))
    page = store.current_fact_page(0, LARGE_COUNT)
    assert len(page.facts) == DEFAULT_PAGE_COUNT
    assert sum(fixture.content_sizes(store)[:DEFAULT_PAGE_COUNT]) <= interpretation_pages.MAX_PAGE_CONTENT_BYTES
    remaining = store.current_fact_page(page.facts[-1].cursor, LARGE_COUNT)
    assert len(remaining.facts) == LARGE_COUNT - DEFAULT_PAGE_COUNT


def test_default_budget_returns_one_large_fact(tmp_path: Path) -> None:
    """A valid four-byte text document can exceed the budget once metadata is added."""
    store = storage.repository(tmp_path)
    encoded = '"{text}"'.format(text="📘" * (documents.MAX_DOCUMENT_CHARACTERS - 2))
    storage.seed(store, (
        storage.fact("large", encoded=encoded), storage.fact(LAST),
    ))
    assert fixture.content_sizes(store)[0] > interpretation_pages.MAX_PAGE_CONTENT_BYTES
    page = store.current_fact_page(0, 10)
    assert fixture.identities(page) == ("large",)
    remaining = store.current_fact_page(page.facts[-1].cursor, 10)
    assert fixture.identities(remaining) == (LAST,)


def test_scope_and_history_filter_before_budget(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Foreign scope and candidate bodies cannot consume the selected page budget."""
    store = storage.repository(tmp_path)
    foreign = scopes.SessionScope(session_id="session", actor_id="actor", harness="test")
    storage.seed(store, (
        storage.fact("first"), storage.fact("foreign", foreign), storage.fact(LAST),
    ))
    storage.seed(store, (storage.fact("candidate"),), "candidate")
    budget = sum(fixture.content_sizes(store)[::2])
    monkeypatch.setattr(interpretation_pages, fixture.BUDGET_FIELD, budget)
    page = fixture.read(store, scoped=True)
    assert fixture.identities(page) == ("first", LAST)
    assert page.head == page.facts[-1].cursor
    remaining = fixture.read(store, page.facts[0].cursor, scoped=True)
    assert fixture.identities(remaining) == (LAST,)


def test_core_drain_handles_oversized_pages(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A byte-limited page does not stop draining or lose later core work."""
    case = reactions.installed(tmp_path, core=False)
    reactions.append_extensions(case, LARGE_COUNT)
    reactions.append_core(case, payloads.started())
    monkeypatch.setattr(interpretation_pages, fixture.BUDGET_FIELD, 1)
    assert case.loop.drain(bool) == LARGE_COUNT + 1
    assert case.view.progress() == LARGE_COUNT + 1
    assert len(case.reaction.seen) == 1
    assert len(case.view.visible()) == 1
    assert not case.loop.tick()
