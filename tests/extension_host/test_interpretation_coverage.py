# Copyright (c) 2026 Zhambyl Yermagambet
"""Reject incomplete transform journals at the actual storage boundary."""

from pathlib import Path

import pytest
from baqylau_extension_api.models import raw_transforms, transforms

from tests import sqlite_migration_fixture as snapshots, storage_reads
from tests.extension_host import (
    interpretation_fixture as fixtures,
    interpretation_raw as raw,
    interpretation_transforms as operations,
)


@pytest.mark.parametrize("stage", ["raw", "canonical"])
def test_eligible_transform_cannot_be_omitted(tmp_path: Path, stage: str) -> None:
    """A valid final fact is not sufficient when the selected transform was skipped."""
    manifest = operations.raw_manifest() if stage == "raw" else operations.manifest()
    case = fixtures.installed(tmp_path, manifest)
    request = fixtures.proposal(case)
    before = snapshots.snapshot(case.store.database)
    with pytest.raises(ValueError, match=f"omitted an eligible {stage} transform"):
        case.store.record_interpretation(request)
    assert snapshots.snapshot(case.store.database) == before


def test_complete_raw_and_canonical_steps_commit(tmp_path: Path) -> None:
    """No-op calls still supply complete evidence for each eligible stage."""
    case = fixtures.installed(tmp_path, operations.combined_manifest())
    request = raw.apply(fixtures.proposal(case), raw_transforms.RawTransformResult())
    request = operations.apply(request, transforms.CanonicalTransformResult())
    assert case.store.record_interpretation(request).accepted
    journal = storage_reads.find_interpretation(case.store, "default", request.proposal.binding.raw_event_id)
    assert journal is not None
    stages = tuple(step.stage for step in journal.proposal.steps)
    assert stages == ("raw", "extension_translation", "canonical")


def test_raw_drop_needs_no_empty_canonical_call(tmp_path: Path) -> None:
    """A downstream transformer is not eligible after earlier raw output is empty."""
    case = fixtures.installed(tmp_path, operations.combined_manifest())
    request = fixtures.proposal(case)
    selected = raw.request(request)
    request = raw.apply(request, raw_transforms.RawTransformResult(operations=(
        transforms.Drop(input_id=selected.inputs[0].input_id, reason="Skip this input"),
    )))
    assert not case.store.record_interpretation(request).accepted
    assert not case.original.store.pending_observations(10)


@pytest.mark.parametrize("field", ["scopes", "input_types"])
def test_nonmatching_transform_is_not_required(tmp_path: Path, field: str) -> None:
    """Only the exact declared source type and scope make a transform eligible."""
    manifest = operations.raw_manifest()
    selected_value = "session" if field == "scopes" else "other"
    selected = operations.RAW_SELECTION.model_copy(update={field: (selected_value,)})
    manifest = manifest.model_copy(update={
        "contributions": manifest.contributions.model_copy(update={"processing": (selected,)}),
    })
    case = fixtures.installed(tmp_path, manifest)
    assert case.store.record_interpretation(fixtures.proposal(case)).accepted
