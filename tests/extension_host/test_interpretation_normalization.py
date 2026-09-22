# Copyright (c) 2026 Zhambyl Yermagambet
"""Check shared-body journal admission and exact normalized reads."""

from pathlib import Path

import pytest
from baqylau_extension_api.models import transforms

from extensions.models import interpretations
from repository.impl.sqlite import interpretation_journal_writes
from tests.extension_host import (
    interpretation_fixture as fixtures,
    interpretation_normalization_fixture as evidence,
    interpretation_transforms as declarations,
    processing_pipeline_fixture as pipeline,
)


def test_large_reply_publishes_shared_bodies(tmp_path: Path) -> None:
    """A valid reply above the old expanded check publishes with one copy of each body."""
    case = pipeline.installed(tmp_path)
    case.probes.canonical.transform.side_effect = evidence.large_reply
    commit = case.run()

    stored = case.original.store.find_interpretation(
        evidence.DEFAULT_HISTORY, commit.proposal.binding.raw_event_id,
    )
    assert len(commit.proposal.facts) == evidence.FACT_COUNT
    assert len(commit.proposal.model_dump_json().encode("utf-8")) > interpretations.MAX_INTERPRETATION_BYTES
    assert evidence.normalized_length(commit.proposal) <= interpretations.MAX_INTERPRETATION_BYTES
    assert stored == commit


def test_shared_bodies_link_once(tmp_path: Path) -> None:
    """Every distinct body of one journal has exactly one ownership link."""
    case = pipeline.installed(tmp_path)
    case.probes.canonical.transform.side_effect = evidence.large_reply
    case.run()

    counts = evidence.body_counts(case.original)
    assert counts[2] == counts[3]


def test_repeated_fact_uses_one_body_version(tmp_path: Path) -> None:
    """One fact that appears in the request and the final facts uses one stored body."""
    case = fixtures.installed(tmp_path, declarations.manifest())
    request = fixtures.proposal(case)
    request = declarations.apply(request, transforms.CanonicalTransformResult(
        operations=(evidence.keep_first_fact(request),),
    ))
    case.store.record_interpretation(request)

    counts = evidence.body_counts(case)
    stored = case.store.find_interpretation(evidence.DEFAULT_HISTORY, request.proposal.binding.raw_event_id)
    assert counts[:2] == (1, 1)
    assert counts[2] == counts[3]
    assert stored == request


def test_oversized_journal_keeps_input_pending(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A rejected normalized journal leaves no fact, body, or cleared input."""
    case = pipeline.installed(tmp_path)
    monkeypatch.setattr(interpretation_journal_writes, "MAX_INTERPRETATION_BYTES", 1)

    with pytest.raises(ValueError, match="normalized size limit"):
        case.run()

    counts = evidence.body_counts(case.original)
    assert evidence.journal_count(case.original) == 0
    assert case.original.original.store.pending_observations(10)
    assert case.original.store.current_fact_page(0, 10).facts == ()
    assert counts[2:] == (0, 0)


def test_new_journal_uses_normalized_read_path(tmp_path: Path) -> None:
    """A new journal stores codec version 2 steps in their own table, not in the old view."""
    case = fixtures.installed(tmp_path)
    request = fixtures.proposal(case)
    case.store.record_interpretation(request)

    codec, view_count, stages = evidence.stored_step_stages(case)
    assert codec == evidence.NORMALIZED_CODEC_VERSION
    assert view_count == 0
    assert stages == ("extension_translation",)
    assert case.store.find_interpretation(
        evidence.DEFAULT_HISTORY, request.proposal.binding.raw_event_id,
    ) == request
