# Copyright (c) 2026 Zhambyl Yermagambet
"""Reject missing or forged required processing inside the actual transaction."""

from hashlib import sha256
from pathlib import Path

import pytest

from domain.records import RecordedTranslationDecision
from tests import sqlite_migration_fixture as snapshots
from tests.extension_host import (
    interpretation_core as core,
    interpretation_fixture as fixtures,
    lifecycle_journal_changes as changes,
)


@pytest.mark.parametrize("indexes", [
    (1,), (0, 0, 1), (1, 0),
])
def test_required_pass_must_be_first_and_single(tmp_path: Path, indexes: tuple[int, ...]) -> None:
    """Missing, repeated, and late lifecycle steps leave the complete database unchanged."""
    case = fixtures.installed(tmp_path)
    request = core.core_proposal(case)
    request = changes.with_steps(request, tuple(
        request.proposal.steps[index] for index in indexes
    ))
    before = snapshots.snapshot(case.store.database)
    with pytest.raises((ValueError, TypeError)):
        case.store.record_interpretation(request)
    assert snapshots.snapshot(case.store.database) == before


@pytest.mark.parametrize(("field", "replacement"), [
    ("content_byte_length", 1234), ("content_digest", sha256(b"changed").hexdigest()),
    ("translator_version", "other-version"),
])
def test_required_step_keeps_original_reference(tmp_path: Path, field: str, replacement: str | int) -> None:
    """A valid but incorrect byte reference or version cannot enter storage."""
    case = fixtures.installed(tmp_path)
    request = core.core_proposal(case)
    changed = changes.required(request).model_copy(update={field: replacement})
    request = changes.with_steps(request, (changed, changes.activity(request)))
    before = snapshots.snapshot(case.store.database)
    with pytest.raises(ValueError, match="required lifecycle step changed"):
        case.store.record_interpretation(request)
    assert snapshots.snapshot(case.store.database) == before


def test_activity_step_cannot_supply_lifecycle(tmp_path: Path) -> None:
    """A core activity step cannot create or repeat required session facts."""
    case = fixtures.installed(tmp_path)
    request = core.core_proposal(case)
    activity = changes.activity(request).model_copy(update={
        "facts": changes.required(request).facts, "decision": RecordedTranslationDecision.TRANSLATED,
    })
    request = changes.with_steps(request, (changes.required(request), activity))
    before = snapshots.snapshot(case.store.database)
    with pytest.raises(ValueError, match="derived activity cannot create required"):
        case.store.record_interpretation(request)
    assert snapshots.snapshot(case.store.database) == before


def test_old_format_cannot_admit_a_new_write(tmp_path: Path) -> None:
    """Read support for old journals cannot bypass current write checks."""
    case = fixtures.installed(tmp_path)
    request = core.core_proposal(case)
    request = request.model_copy(update={"proposal": request.proposal.model_copy(update={
        "format_version": 1,
    })})
    database = case.store.database
    before = snapshots.snapshot(database)
    with pytest.raises(ValueError, match="current lifecycle-aware format"):
        case.store.record_interpretation(request)
    assert snapshots.snapshot(database) == before
