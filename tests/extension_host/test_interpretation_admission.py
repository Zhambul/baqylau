# Copyright (c) 2026 Zhambyl Yermagambet
"""Check admission before worker calls and before result application."""

import hashlib
from pathlib import Path

import pytest

from domain.records import RecordedTranslationDecision
from extensions.models import interpretation_steps as steps
from tests.extension_host import (
    interpretation_admission_fixture as admission,
    lifecycle_pipeline_fixture as lifecycle,
    processing_pipeline_fixture as pipeline,
)
from tests.plugin_tests import translation_stage_fixture as native

ORIGINAL_TEXT = "original"


def test_exhausted_budget_records_no_call(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A full journal records the selected calls without invoking any worker."""
    case = pipeline.installed(
        tmp_path, admission_limit=admission.select_limit(monkeypatch, admission.RESERVE_BUDGET),
    )
    commit = case.run()

    case.probes.raw.transform.assert_not_called()
    case.probes.decoder.translate.assert_not_called()
    case.probes.canonical.transform.assert_not_called()
    assert admission.limit_stages(commit) == ("raw", "extension_translation")


def test_exhausted_budget_verdict(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A journal limit keeps a failed verdict and an exact stored record."""
    case = pipeline.installed(
        tmp_path, admission_limit=admission.select_limit(monkeypatch, admission.RESERVE_BUDGET),
    )
    commit = case.run()

    assert commit.proposal.decision == RecordedTranslationDecision.TRANSLATION_FAILED
    assert not commit.proposal.facts
    assert admission.stored_commit(case) == commit


def test_aggregate_budget_limits_later_call(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An admitted large raw reply consumes the budget for the translation call."""
    measured = pipeline.installed(tmp_path / "measured")
    measured.probes.raw.transform.side_effect = admission.large_raw_reply
    measured.run()
    limit = admission.raw_step_budget(measured)

    case = pipeline.installed(tmp_path / "limited", admission_limit=admission.select_limit(monkeypatch, limit))
    case.probes.raw.transform.side_effect = admission.large_raw_reply
    commit = case.run()

    assert admission.limit_stages(commit) == ("extension_translation",)
    case.probes.decoder.translate.assert_not_called()
    case.probes.canonical.transform.assert_not_called()


def test_oversized_reply_records_evidence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A reply above the call budget records its exact size and digest."""
    case = pipeline.installed(
        tmp_path, admission_limit=admission.select_limit(monkeypatch, admission.REPLY_BUDGET),
    )
    case.probes.canonical.transform.side_effect = admission.large_canonical_reply
    case.run()

    outcome, encoded = admission.rejection_evidence(case)
    assert outcome.observed_byte_length == len(encoded)
    assert outcome.observed_digest == hashlib.sha256(encoded).hexdigest()


def test_oversized_reply_keeps_earlier_facts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A rejected reply cannot change the facts accepted before it."""
    case = pipeline.installed(
        tmp_path, admission_limit=admission.select_limit(monkeypatch, admission.REPLY_BUDGET),
    )
    case.probes.canonical.transform.side_effect = admission.large_canonical_reply
    case.run()

    commit = admission.stored_commit(case)
    final_ids = {fact.event_id for fact in commit.proposal.facts}
    assert commit.proposal.facts
    assert commit.proposal.decision == RecordedTranslationDecision.TRANSLATED
    assert not (admission.rejected_fact_ids(case) & final_ids)


def test_small_budget_keeps_required_core_work(tmp_path: Path) -> None:
    """A small budget still keeps and publishes the original lifecycle facts."""
    case = lifecycle.installed(tmp_path, native.claude_prompt(ORIGINAL_TEXT), admission_limit=admission.SMALL_BUDGET)
    commit = case.pipeline.run()

    required = commit.proposal.steps[0]
    assert isinstance(required, steps.CoreLifecycleStep)
    assert required.facts
    assert admission.stored_commit(case.pipeline) == commit
