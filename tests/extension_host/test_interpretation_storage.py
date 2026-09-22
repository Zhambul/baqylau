# Copyright (c) 2026 Zhambyl Yermagambet
"""Check complete journal and fact acceptance through the public repository protocol."""

from pathlib import Path

from domain.ids import CanonicalEventId
from tests.extension_host import interpretation_fixture as fixtures

HISTORY = "default"


def test_fact_commits_with_complete_journal(tmp_path: Path) -> None:
    """Accept exact extension content, one source link, the journal, and decoder progress together."""
    case = fixtures.installed(tmp_path)
    request = fixtures.proposal(case)
    outcome = case.store.record_interpretation(request)
    assert not outcome.repeated and not outcome.deduplicated
    assert outcome.accepted[0].fact == request.proposal.facts[0]
    assert case.store.find_interpretation(HISTORY, request.proposal.binding.raw_event_id) == request
    assert not case.original.store.pending_observations(10)
    assert case.store.translator_state(fixtures.state_key(case)).revision == 1


def test_retry_retains_time_and_state(tmp_path: Path) -> None:
    """An exact request retry does not create facts, advance state, or repeat acceptance reactions."""
    case = fixtures.installed(tmp_path)
    request = fixtures.proposal(case)
    first = case.store.record_interpretation(request)
    request = request.model_copy(update={"completed_at": request.completed_at + 1})
    repeated = case.store.record_interpretation(request)
    assert repeated.repeated and not repeated.accepted
    assert repeated.deduplicated == first.accepted
    assert case.store.translator_state(fixtures.state_key(case)).revision == 1
    journal = case.store.find_interpretation(HISTORY, request.proposal.binding.raw_event_id)
    assert journal is not None and journal.completed_at == request.completed_at - 1


def test_mixed_page_returns_one_read_boundary(tmp_path: Path) -> None:
    """The page head and accepted rows use the same explicit history."""
    case = fixtures.installed(tmp_path)
    request = fixtures.proposal(case)
    accepted = case.store.record_interpretation(request).accepted
    page = case.store.current_fact_page(0, 10)
    assert page.facts == accepted and page.head == accepted[0].cursor
    assert case.store.facts_for_scope(HISTORY, request.proposal.binding.scope, 0, 10) == page
    event_id = CanonicalEventId(request.proposal.facts[0].event_id)
    assert case.store.find_fact(HISTORY, event_id) == accepted[0]
