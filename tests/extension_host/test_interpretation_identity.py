# Copyright (c) 2026 Zhambyl Yermagambet
"""Preserve first acceptance while retaining every later logical proposal."""

from pathlib import Path

import pytest
from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.models.events import ExtensionFact

from tests import sqlite_migration_fixture as snapshots, storage_reads
from tests.extension_host import (
    interpretation_fixture as fixtures,
    interpretation_results as evidence,
    observation_requests as originals,
)

STATE_REVISION = 2


def test_later_body_is_retained_but_not_accepted(tmp_path: Path) -> None:
    """A second source observation links to the first body and keeps its changed proposal."""
    case = fixtures.installed(tmp_path)
    first = case.store.record_interpretation(fixtures.proposal(case))
    request = fixtures.proposal(case, originals.document(
        originals.new_key(case.original.request, "later"), ' "changed body"\n',
    ))
    outcome = case.store.record_interpretation(request)
    assert not outcome.accepted and outcome.deduplicated == first.accepted
    assert storage_reads.find_interpretation(case.store, "default", request.proposal.binding.raw_event_id) == request
    assert request.proposal.facts[0] != outcome.deduplicated[0].fact
    assert case.store.translator_state(fixtures.state_key(case)).revision == STATE_REVISION


@pytest.mark.parametrize("encoded", ["42", "not-json", '{"unexpected":true}'])
def test_invalid_fact_document_keeps_pending(tmp_path: Path, encoded: str) -> None:
    """Host acceptance validates a decoder document against the retained event schema."""
    case = fixtures.installed(tmp_path)
    request = fixtures.proposal(case)
    fact = request.proposal.facts[0]
    assert isinstance(fact, ExtensionFact)
    before = snapshots.snapshot(case.store.database)
    fact = fact.model_copy(update={"document": fact.document.model_copy(
        update={"json_text": encoded},
    )})
    with pytest.raises(ExtensionContractError):
        case.store.record_interpretation(evidence.with_fact(request, fact))
    assert snapshots.snapshot(case.store.database) == before


@pytest.mark.parametrize("cause", ["missing-parent", "self"])
def test_invalid_cause_keeps_pending(tmp_path: Path, cause: str) -> None:
    """Missing references and self-reference cannot enter canonical storage."""
    case = fixtures.installed(tmp_path)
    request = fixtures.proposal(case)
    fact = request.proposal.facts[0]
    assert isinstance(fact, ExtensionFact)
    before = snapshots.snapshot(case.store.database)
    fact = fact.model_copy(update={
        "causes": (fact.event_id if cause == "self" else cause,),
    })
    with pytest.raises(ValueError, match=r"cause is not recorded|must not refer to this event"):
        case.store.record_interpretation(evidence.with_fact(request, fact))
    assert snapshots.snapshot(case.store.database) == before
