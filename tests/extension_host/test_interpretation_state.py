# Copyright (c) 2026 Zhambyl Yermagambet
"""Compare complete decoder state and retain it atomically with the fact journal."""

from pathlib import Path

import pytest

from tests import sqlite_migration_fixture as snapshots
from tests.extension_api import operation_samples
from tests.extension_host import (
    interpretation_fixture as fixtures,
    interpretation_results as evidence,
    observation_requests as originals,
)


def test_decoder_document_round_trips(tmp_path: Path) -> None:
    """A complete next state commits with its exact bytes and one host revision."""
    case = fixtures.installed(tmp_path)
    request = fixtures.proposal(case)
    document = operation_samples.query_request('  "state é"\n').arguments
    request = evidence.with_reply(request, evidence.reply(request).model_copy(
        update={"next_state": document},
    ))
    case.store.record_interpretation(request)
    state = case.store.translator_state(fixtures.state_key(case))
    assert state.document == document and state.revision == 1
    case.store.record_interpretation(request)
    assert case.store.translator_state(fixtures.state_key(case)) == state


def test_stale_state_rejects_even_at_new_head(tmp_path: Path) -> None:
    """Changing only the canonical boundary cannot authorize an old decoder state."""
    case = fixtures.installed(tmp_path)
    first = fixtures.proposal(case)
    request = fixtures.proposal(case, originals.new_key(case.original.request, "next"))
    case.store.record_interpretation(first)
    request = request.model_copy(update={"proposal": request.proposal.model_copy(update={
        "binding": request.proposal.binding.model_copy(update={
            "expected_canonical_cursor": case.store.current_fact_page(0, 1).head,
        }),
    })})
    before = snapshots.snapshot(case.store.database)
    with pytest.raises(ValueError, match="decoder state is stale"):
        case.store.record_interpretation(request)
    assert snapshots.snapshot(case.store.database) == before
