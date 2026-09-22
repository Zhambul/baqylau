# Copyright (c) 2026 Zhambyl Yermagambet
"""Read and retry stored pre-split journals without rewriting their bytes."""

from pathlib import Path

import pytest

from extensions.models import interpretation_steps as steps, interpretations
from tests import sqlite_migration_fixture as snapshots
from tests.extension_host import interpretation_fixture as fixtures, lifecycle_legacy_fixture as legacy


@pytest.mark.parametrize("core_input", [True, False])
def test_old_inline_journal_still_reads(tmp_path: Path, *, core_input: bool) -> None:
    """The absent version field selects the old model and retains complete stored bodies."""
    case = fixtures.installed(tmp_path)
    request = legacy.stored_legacy(case, core_input=core_input)
    before = snapshots.snapshot(case.store.database)
    stored = case.store.find_interpretation("default", request.proposal.binding.raw_event_id)
    assert stored == request
    assert stored.proposal.format_version == 1
    assert isinstance(
        stored.proposal.steps[0], steps.CoreTranslationStep if core_input else steps.ExtensionTranslationStep,
    )
    assert snapshots.snapshot(case.store.database) == before


@pytest.mark.parametrize("core_input", [True, False])
def test_old_exact_retry_does_not_write(tmp_path: Path, *, core_input: bool) -> None:
    """An exact old retry returns first acceptance without new steps, facts, or cleanup."""
    case = fixtures.installed(tmp_path)
    request = legacy.stored_legacy(case, core_input=core_input)
    before = snapshots.snapshot(case.store.database)
    accepted = case.store.current_fact_page(0, 10).facts
    outcome = case.store.record_interpretation(request)
    assert outcome.repeated and not outcome.accepted
    assert outcome.deduplicated == accepted
    assert snapshots.snapshot(case.store.database) == before


def test_old_size_limit_uses_old_shape(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An old journal keeps its inline shape; the normalized limit does not reject it on read."""
    case = fixtures.installed(tmp_path)
    request = legacy.stored_legacy(case, core_input=True)
    encoded = request.proposal.model_dump_json(exclude={"format_version"})
    monkeypatch.setattr(interpretations, "MAX_INTERPRETATION_BYTES", len(encoded.encode("utf-8")))
    assert interpretations.InterpretationProposal.model_validate_json(encoded) == request.proposal
