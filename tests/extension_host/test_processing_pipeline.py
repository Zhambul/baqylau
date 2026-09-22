# Copyright (c) 2026 Zhambyl Yermagambet
"""Check full raw, decoder, and canonical processing before real fact acceptance."""

from functools import partial
from pathlib import Path

import pytest

from domain.records import RecordedTranslationDecision
from extensions.models import interpretation_steps as steps
from tests.extension_host import processing_operations as operations, processing_pipeline_fixture as fixture


def test_complete_pipeline_records_each_stage(tmp_path: Path) -> None:
    """Every eligible capability is called once and its checked step is committed."""
    case = fixture.installed(tmp_path)
    result = case.run()
    stages = tuple(step.stage for step in result.proposal.steps)
    assert stages == ("raw", "extension_translation", "canonical")
    assert result.proposal.decision == RecordedTranslationDecision.TRANSLATED
    assert not case.original.original.store.pending_observations(10)
    case.probes.core.translate_input.assert_not_called()


def test_raw_replace_retains_original(tmp_path: Path) -> None:
    """The decoder receives replacement bytes; original storage is unchanged."""
    case = fixture.installed(tmp_path)
    case.probes.raw.transform.side_effect = partial(operations.raw_replace, encoded=b'"changed"')
    result = case.run()
    fact = result.proposal.facts[0]
    assert fact.kind == "extension" and fact.document.json_text == '"changed"'
    assert case.original.original.store.find_observation(case.stored.observation.raw_event_id) == case.stored


def test_bad_raw_document_keeps_input(tmp_path: Path) -> None:
    """A structurally valid raw reply cannot send a wrong-schema document to the decoder."""
    case = fixture.installed(tmp_path)
    case.probes.raw.transform.side_effect = partial(operations.raw_replace, encoded=b"{}")
    result = case.run()
    step = result.proposal.steps[0]
    assert isinstance(step, steps.RawTransformStep) and isinstance(step.outcome, steps.FailedStep)
    assert step.outcome.reply is not None
    assert result.proposal.decision == RecordedTranslationDecision.TRANSLATED


def test_raw_drop_skips_decoder_and_canonical(tmp_path: Path) -> None:
    """All-dropped raw input has a complete suppression verdict and no empty calls."""
    case = fixture.installed(tmp_path)
    case.probes.raw.transform.side_effect = operations.raw_drop
    result = case.run()
    assert result.proposal.decision == RecordedTranslationDecision.SUPPRESSED
    assert len(result.proposal.steps) == 1
    case.probes.decoder.translate.assert_not_called()
    case.probes.canonical.transform.assert_not_called()


@pytest.mark.parametrize("insert", [False, True])
def test_canonical_drop_retains_decoder(tmp_path: Path, *, insert: bool) -> None:
    """The complete translated fact stays in the journal even when final output changes."""
    case = fixture.installed(tmp_path)
    case.probes.canonical.transform.side_effect = operations.canonical_insert if insert else operations.canonical_drop
    result = case.run()
    translation = result.proposal.steps[1]
    assert isinstance(translation, steps.ExtensionTranslationStep)
    assert isinstance(translation.outcome, steps.AppliedStep)
    assert bool(result.proposal.facts) == insert
    assert case.original.store.current_fact_page(0, 10).head == int(insert)


def test_failed_decoder_records_terminal_result(tmp_path: Path) -> None:
    """A failed pure call clears only this pending original and does not call canonical transforms."""
    case = fixture.installed(tmp_path)
    case.probes.decoder.translate.side_effect = RuntimeError("fixture failure")
    result = case.run()
    assert result.proposal.decision == RecordedTranslationDecision.TRANSLATION_FAILED
    assert not case.original.original.store.pending_observations(10)
    case.probes.canonical.transform.assert_not_called()
