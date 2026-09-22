# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep strict core behavior and a single ordered mixed canonical stream."""

from pathlib import Path

import pytest
from baqylau_extension_api.models import canonical, events, transforms

from repository.impl.sqlite.canonical_events import SqliteCanonicalEventRepository
from tests import sqlite_migration_fixture as snapshots, sqlite_test_fixtures as native
from tests.extension_host import (
    interpretation_core as core,
    interpretation_fixture as fixtures,
    interpretation_transforms as operations,
)


def test_core_translation_uses_existing_codec(tmp_path: Path) -> None:
    """Core input and payloads use the same private model and legacy readers."""
    case = fixtures.installed(tmp_path)
    request = core.core_proposal(case)
    accepted = case.store.record_interpretation(request).accepted
    legacy = SqliteCanonicalEventRepository(case.store.database).page_from(0, 10)
    assert isinstance(accepted[0].fact, canonical.CoreFact)
    assert legacy[0].event_id == accepted[0].fact.event_id
    assert legacy[0].payload == native.a_started_event().payload
    found = SqliteCanonicalEventRepository(case.store.database).find(legacy[0].event_id)
    assert found is not None and found.raw_event_ids == (request.proposal.binding.raw_event_id,)
    assert case.store.find_interpretation("default", request.proposal.binding.raw_event_id) == request


def test_core_and_extension_commit_in_order(tmp_path: Path) -> None:
    """One transaction gives both branches a shared canonical arrival order."""
    case = fixtures.installed(tmp_path)
    request = core.mixed_proposal(case)
    accepted = case.store.record_interpretation(request).accepted
    assert isinstance(accepted[0].fact, canonical.CoreFact)
    assert isinstance(accepted[1].fact, events.ExtensionFact)
    assert accepted[0].cursor < accepted[1].cursor
    assert case.store.current_fact_page(0, 10).facts == accepted
    legacy = SqliteCanonicalEventRepository(case.store.database).page_from(0, 10)
    assert tuple(fact.event_id for fact in legacy) == (
        accepted[0].fact.event_id,
    )


def test_core_drop_keeps_extension_fact(tmp_path: Path) -> None:
    """A core drop leaves the other fact at its original relative position."""
    case = fixtures.installed(tmp_path, operations.manifest())
    request = core.mixed_proposal(case)
    request = operations.apply(request, transforms.CanonicalTransformResult(operations=(
        transforms.Drop(input_id=request.proposal.facts[0].event_id, reason="Hide title"),
    )))
    accepted = case.store.record_interpretation(request).accepted
    assert len(accepted) == 1
    assert isinstance(accepted[0].fact, events.ExtensionFact)


def test_core_finish_cannot_be_suppressed(tmp_path: Path) -> None:
    """Even a later drop cannot make a forged required finish valid."""
    case = fixtures.installed(tmp_path, operations.manifest())
    request = core.mixed_proposal(case, finished=True)
    request = operations.apply(request, transforms.CanonicalTransformResult(operations=(
        transforms.Drop(input_id=request.proposal.facts[0].event_id, reason="Hide finish"),
    )))
    before = snapshots.snapshot(case.store.database)
    with pytest.raises(ValueError, match="derived activity cannot create required session lifecycle"):
        case.store.record_interpretation(request)
    assert snapshots.snapshot(case.store.database) == before
