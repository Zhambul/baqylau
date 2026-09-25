# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep unavailable originals while committing a complete checked terminal verdict."""

from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import pytest
from baqylau_extension_api.models.content import MAX_CONTENT_BYTES

from domain.records import RecordedTranslationDecision
from extensions.models import interpretation_context, interpretation_unavailable as preflight, observations
from extensions.models.interpretation_steps import UnavailableInputReason, UnavailableInputStep
from extensions.models.interpretations import InterpretationCommit
from repository.impl.sqlite.raw_events import SqliteRawEventRepository
from tests import sqlite_migration_fixture as snapshots, sqlite_test_fixtures as native, storage_reads
from tests.extension_host import (
    interpretation_context_fixture as contexts,
    interpretation_fixture as fixtures,
    lifecycle_fixture,
    lifecycle_pipeline_fixture,
    observation_requests,
)

COMPLETED_AT = 1001


def test_large_core_input_keeps_original_bytes(tmp_path: Path) -> None:
    """A raw value above the transfer limit gets a stored failure without truncation."""
    case = fixtures.installed(tmp_path)
    raw = replace(native.a_raw_event(), payload=b"x" * (MAX_CONTENT_BYTES + 1))
    SqliteRawEventRepository(case.store.database).record((raw,))
    stored = case.original.store.find_observation(raw.raw_event_id)
    assert stored is not None
    request = _request(contexts.context(case, stored))
    assert tuple(step.stage for step in request.proposal.steps) == ("core_lifecycle", "unavailable_input")
    assert not case.store.record_interpretation(request).accepted
    assert case.original.store.find_observation(raw.raw_event_id) == stored
    assert not case.original.store.pending_observations(10)


def test_limit_uses_encoded_bytes_not_characters(tmp_path: Path) -> None:
    """An accepted Unicode document can exceed the worker byte limit without exceeding its text limit."""
    case = fixtures.installed(tmp_path)
    text = "é" * (MAX_CONTENT_BYTES // 2 + 1)
    stored = storage_reads.append_observations(case.original.store,
        observation_requests.document(case.original.request, f'"{text}"'),
    ).accepted[0]
    request = _request(contexts.context(case, stored))
    assert request.proposal.decision == RecordedTranslationDecision.TRANSLATION_FAILED
    case.store.record_interpretation(request)
    assert storage_reads.find_interpretation(case.store, "default", stored.observation.raw_event_id) == request
    assert case.original.store.find_observation(stored.observation.raw_event_id) == stored


def test_disabled_owner_gets_unknown_verdict(tmp_path: Path) -> None:
    """Owner removal does not leave the original permanently at the pending queue head."""
    case = fixtures.installed(tmp_path)
    stored = storage_reads.append_observations(case.original.store, case.original.request).accepted[0]
    lifecycle_fixture.commit(case.original.lifecycle, lifecycle_fixture.proposal(
        case.original.lifecycle, operation_id="empty-runtime",
    ))
    request = _request(contexts.context(case, stored))
    assert request.proposal.decision == RecordedTranslationDecision.IGNORED_UNKNOWN
    assert not case.store.record_interpretation(request).accepted
    assert not case.original.store.pending_observations(10)
    assert case.original.store.find_observation(stored.observation.raw_event_id) == stored


@pytest.mark.parametrize("reason", ["content_limit", "owner_disabled"])
def test_available_input_rejects_false_failure(tmp_path: Path, reason: UnavailableInputReason) -> None:
    """A caller cannot skip transforms or translation through a fabricated preflight result."""
    case = fixtures.installed(tmp_path)
    stored = storage_reads.append_observations(case.original.store, case.original.request).accepted[0]
    step = UnavailableInputStep(reason=reason, content_byte_length=1, content_digest=sha256(b"x").hexdigest())
    request = InterpretationCommit(
        proposal=preflight.unavailable_proposal(contexts.context(case, stored), step), completed_at=COMPLETED_AT,
    )
    before = snapshots.snapshot(case.store.database)
    with pytest.raises(ValueError, match="does not match its original"):
        case.store.record_interpretation(request)
    assert snapshots.snapshot(case.store.database) == before


def _request(context: interpretation_context.InterpretationContext) -> InterpretationCommit:
    step = preflight.unavailable_input(context)
    assert step is not None
    original = context.original.observation
    required = None if isinstance(original, observations.ExtensionObservation) else (
        lifecycle_pipeline_fixture.phase().phase.translate_lifecycle(original)
    )
    return InterpretationCommit(
        proposal=preflight.unavailable_proposal(context, step, required), completed_at=COMPLETED_AT,
    )
