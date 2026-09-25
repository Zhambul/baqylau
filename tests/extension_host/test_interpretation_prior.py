# Copyright (c) 2026 Zhambyl Yermagambet
"""Reject fabricated prior facts without changing any interpretation state."""

from pathlib import Path

import pytest
from baqylau_extension_api.models.events import ExtensionFact

from tests import sqlite_migration_fixture as snapshots, storage_reads
from tests.extension_host import (
    interpretation_fixture as fixtures,
    interpretation_prior as prior,
    interpretation_transforms as operations,
)


def test_actual_prior_fact_is_accepted(tmp_path: Path) -> None:
    """A captured prior fact can be used by a later canonical transform."""
    case = fixtures.installed(tmp_path, operations.manifest())
    request = prior.proposal(case)
    outcome = case.store.record_interpretation(request)
    assert not outcome.accepted
    assert outcome.deduplicated[0].fact == prior.prior_fact(request).fact
    assert storage_reads.find_interpretation(case.store, "default", request.proposal.binding.raw_event_id) == request


def test_changed_prior_time_is_rejected(tmp_path: Path) -> None:
    """A real fact ID cannot authorize a fabricated acceptance time."""
    case = fixtures.installed(tmp_path, operations.manifest())
    request = prior.proposal(case)
    selected = prior.prior_fact(request)
    request = prior.with_prior(request, selected.model_copy(
        update={"accepted_at": selected.accepted_at + 1},
    ))
    before = snapshots.snapshot(case.store.database)
    with pytest.raises(ValueError, match="different prior fact snapshot"):
        case.store.record_interpretation(request)
    assert snapshots.snapshot(case.store.database) == before


@pytest.mark.parametrize("field", ["event_id", "document"])
def test_changed_prior_body_is_rejected(tmp_path: Path, field: str) -> None:
    """A missing prior ID or a changed body is not a valid storage snapshot."""
    case = fixtures.installed(tmp_path, operations.manifest())
    request = prior.proposal(case)
    selected = prior.prior_fact(request)
    fact = selected.fact
    assert isinstance(fact, ExtensionFact)
    fact = fact.model_copy(update={field: (
        "missing-fact" if field == "event_id" else fact.document.model_copy(update={"json_text": '"changed prior"'})
    )})
    request = prior.with_prior(request, selected.model_copy(update={"fact": fact}))
    before = snapshots.snapshot(case.store.database)
    with pytest.raises(ValueError, match="different prior fact snapshot"):
        case.store.record_interpretation(request)
    assert snapshots.snapshot(case.store.database) == before
