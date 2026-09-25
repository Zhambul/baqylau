# Copyright (c) 2026 Zhambyl Yermagambet
"""Accept complete canonical transforms while retaining their original proposals."""

from pathlib import Path

import pytest
from baqylau_extension_api.models import documents, events, transforms

from domain.records import RecordedTranslationDecision
from extensions.models.interpretation_steps import CanonicalTransformStep, FailedStep
from tests import sqlite_migration_fixture as snapshots, storage_reads
from tests.extension_host import interpretation_fixture as fixtures, interpretation_transforms as operations

DEFAULT_HISTORY = "default"


def test_canonical_replacement_keeps_trace(tmp_path: Path) -> None:
    """A replacement changes accepted content, not the translation evidence."""
    case = fixtures.installed(tmp_path, operations.manifest())
    original = fixtures.proposal(case)
    fact = original.proposal.facts[0]
    assert isinstance(fact, events.ExtensionFact)
    fact = fact.model_copy(update={"document": fact.document.model_copy(
        update={"json_text": ' "replacement"\n'},
    )})
    request = operations.apply(original, transforms.CanonicalTransformResult(operations=(
        transforms.Replace(input_id=fact.event_id, document=fact),
    )))
    assert case.store.record_interpretation(request).accepted[0].fact == fact
    assert request.proposal.steps[0] == original.proposal.steps[0]
    assert storage_reads.find_interpretation(
        case.store, DEFAULT_HISTORY, request.proposal.binding.raw_event_id,
    ) == request


def test_all_dropped_commits_complete_verdict(tmp_path: Path) -> None:
    """An intentional drop clears pending work and advances decoder state without a fact."""
    case = fixtures.installed(tmp_path, operations.manifest())
    request = fixtures.proposal(case)
    request = operations.apply(request, transforms.CanonicalTransformResult(operations=(
        transforms.Drop(input_id=request.proposal.facts[0].event_id, reason="Hidden by test"),
    )))
    assert not case.store.record_interpretation(request).accepted
    assert not case.original.store.pending_observations(10)
    assert case.store.translator_state(fixtures.state_key(case)).revision == 1
    assert request.proposal.decision == RecordedTranslationDecision.SUPPRESSED
    assert storage_reads.find_interpretation(
        case.store, DEFAULT_HISTORY, request.proposal.binding.raw_event_id,
    ) == request


def test_addition_can_keep_a_dropped_cause(tmp_path: Path) -> None:
    """The journal retains an intermediate cause that is not a final stored fact."""
    case = fixtures.installed(tmp_path, operations.manifest())
    request = fixtures.proposal(case)
    addition = operations.insertion(request.proposal.facts[0])
    request = operations.apply(request, transforms.CanonicalTransformResult(operations=(
        addition, transforms.Drop(input_id=addition.input_id, reason="Use the added fact"),
    )))
    assert case.store.record_interpretation(request).accepted[0].fact == addition.document
    stored = storage_reads.find_interpretation(case.store, DEFAULT_HISTORY, request.proposal.binding.raw_event_id)
    assert stored is not None
    assert tuple(step.stage for step in stored.proposal.steps) == ("extension_translation", "canonical")


def test_failed_transform_preserves_candidates(tmp_path: Path) -> None:
    """A failed call keeps its input and failure record without applying its reply."""
    case = fixtures.installed(tmp_path, operations.manifest())
    request = fixtures.proposal(case)
    failed = CanonicalTransformStep(request=operations.request(request), outcome=FailedStep(
        diagnostic=documents.Diagnostic(code="timeout", message="The worker timed out"),
        reply=transforms.CanonicalTransformResult(operations=(
            transforms.Drop(input_id=request.proposal.facts[0].event_id, reason="Unaccepted proposal"),
        )),
    ))
    request = request.model_copy(update={"proposal": request.proposal.model_copy(update={
        "steps": (*request.proposal.steps, failed),
    })})
    accepted = case.store.record_interpretation(request).accepted
    assert accepted[0].fact == request.proposal.facts[0]
    assert storage_reads.find_interpretation(
        case.store, DEFAULT_HISTORY, request.proposal.binding.raw_event_id,
    ) == request


def test_owner_cannot_run_twice_in_one_stage(tmp_path: Path) -> None:
    """A repeated transform cannot reprocess its own additions."""
    case = fixtures.installed(tmp_path, operations.manifest())
    request = operations.apply(fixtures.proposal(case), transforms.CanonicalTransformResult())
    request = request.model_copy(update={"proposal": request.proposal.model_copy(update={
        "steps": (*request.proposal.steps, request.proposal.steps[-1]),
    })})
    before = snapshots.snapshot(case.store.database)
    with pytest.raises(ValueError, match="without repeated owners"):
        case.store.record_interpretation(request)
    assert snapshots.snapshot(case.store.database) == before
