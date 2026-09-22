# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep failed and unknown decoder input distinct from intentional suppression."""

from pathlib import Path

import pytest
from baqylau_extension_api.models import documents, translation_results

from domain.records import RecordedTranslationDecision as Decision
from extensions.models.interpretation_steps import FailedStep
from tests import sqlite_migration_fixture as snapshots
from tests.extension_host import interpretation_fixture as fixtures, interpretation_results as evidence

FAILURE = documents.Diagnostic(code="test-failure", message="The decoder failed")
DECISIONS = (
    (
        translation_results.IgnoredInput(input_id="placeholder", reason="Known empty input"),
        Decision.IGNORED_NONSEMANTIC,
    ),
    (translation_results.UnsupportedInput(input_id="placeholder", diagnostic=FAILURE), Decision.IGNORED_UNKNOWN),
    (translation_results.FailedInput(input_id="placeholder", diagnostic=FAILURE), Decision.TRANSLATION_FAILED),
)


@pytest.mark.parametrize(("decision", "verdict"), DECISIONS)
def test_empty_verdict_keeps_complete_evidence(
    tmp_path: Path, decision: translation_results.TranslationDecision, verdict: Decision,
) -> None:
    """A complete empty decoder result clears pending input and advances captured state once."""
    case = fixtures.installed(tmp_path)
    request = fixtures.proposal(case)
    response = evidence.reply(request)
    decision = decision.model_copy(update={"input_id": response.decisions[0].input_id})
    request = evidence.empty_decision(
        request, response.model_copy(update={"decisions": (decision,)}), verdict,
    )
    assert not case.store.record_interpretation(request).accepted
    assert case.store.translator_state(fixtures.state_key(case)).revision == 1
    assert not case.original.store.pending_observations(10)
    assert case.store.find_interpretation("default", request.proposal.binding.raw_event_id) == request


@pytest.mark.parametrize(("decision", "verdict"), DECISIONS)
def test_drop_cannot_hide_decoder_decision(
    tmp_path: Path, decision: translation_results.TranslationDecision, verdict: Decision,
) -> None:
    """A caller cannot label a failed, unknown, or ignored decoder result as a transform drop."""
    case = fixtures.installed(tmp_path)
    request = fixtures.proposal(case)
    response = evidence.reply(request)
    decision = decision.model_copy(update={"input_id": response.decisions[0].input_id})
    request = evidence.empty_decision(
        request, response.model_copy(update={"decisions": (decision,)}), Decision.SUPPRESSED,
    )
    before = snapshots.snapshot(case.store.database)
    assert verdict != Decision.SUPPRESSED
    with pytest.raises(ValueError, match="verdict does not match"):
        case.store.record_interpretation(request)
    assert snapshots.snapshot(case.store.database) == before


def test_failed_call_does_not_advance_state(tmp_path: Path) -> None:
    """Failure without an applied decoder reply retains the captured state."""
    case = fixtures.installed(tmp_path)
    request = fixtures.proposal(case)
    step = evidence.translation(request).model_copy(update={
        "outcome": FailedStep(diagnostic=FAILURE),
    })
    request = request.model_copy(update={"proposal": request.proposal.model_copy(update={
        "steps": (step,), "facts": (), "decision": Decision.TRANSLATION_FAILED, "reason": FAILURE.message,
    })})
    assert not case.store.record_interpretation(request).accepted
    assert case.store.translator_state(fixtures.state_key(case)).revision == 0
    assert case.store.find_interpretation("default", request.proposal.binding.raw_event_id) == request
