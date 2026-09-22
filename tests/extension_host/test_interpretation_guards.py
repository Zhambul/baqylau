# Copyright (c) 2026 Zhambyl Yermagambet
"""Reject stale runtime and input selections without losing pending work."""

from pathlib import Path

import pytest

from tests import sqlite_migration_fixture as snapshots
from tests.extension_host import interpretation_fixture as fixtures, observation_requests as originals

PROPOSAL = "proposal"


@pytest.mark.parametrize("field", ["manager_id", "runtime_revision"])
def test_wrong_runtime_rejects_before_write(tmp_path: Path, field: str) -> None:
    """Manager and runtime identity are compared under the write lock."""
    case = fixtures.installed(tmp_path)
    request = fixtures.proposal(case)
    database = case.store.database
    before = snapshots.snapshot(database)
    binding = request.proposal.binding.model_copy(update={field: "wrong"})
    request = request.model_copy(update={PROPOSAL: request.proposal.model_copy(
        update={"binding": binding},
    )})
    with pytest.raises(ValueError, match="committed runtime"):
        case.store.record_interpretation(request)
    assert snapshots.snapshot(database) == before


@pytest.mark.parametrize("field", ["input_cursor", "expected_canonical_cursor"])
def test_wrong_cursor_rejects_before_write(tmp_path: Path, field: str) -> None:
    """A caller cannot commit a result against another raw row or canonical boundary."""
    case = fixtures.installed(tmp_path)
    request = fixtures.proposal(case)
    database = case.store.database
    before = snapshots.snapshot(database)
    binding = request.proposal.binding.model_copy(update={field: 100})
    request = request.model_copy(update={PROPOSAL: request.proposal.model_copy(
        update={"binding": binding},
    )})
    with pytest.raises(ValueError, match=r"input cursor|snapshot is stale"):
        case.store.record_interpretation(request)
    assert snapshots.snapshot(database) == before


def test_changed_retry_cannot_replace_journal(tmp_path: Path) -> None:
    """One interpreted original retains its complete first proposal."""
    case = fixtures.installed(tmp_path)
    request = fixtures.proposal(case)
    case.store.record_interpretation(request)
    before = snapshots.snapshot(case.store.database)
    request = request.model_copy(update={PROPOSAL: request.proposal.model_copy(
        update={"reason": "Changed"},
    )})
    with pytest.raises(ValueError, match="another complete proposal"):
        case.store.record_interpretation(request)
    assert snapshots.snapshot(case.store.database) == before


@pytest.mark.parametrize("already_committed", [False, True])
def test_new_runtime_rejects_late_result(tmp_path: Path, *, already_committed: bool) -> None:
    """Even an exact old retry cannot bypass a changed runtime selection."""
    case = fixtures.installed(tmp_path)
    request = fixtures.proposal(case)
    if already_committed:
        case.store.record_interpretation(request)
    originals.reload_request(case.original)
    before = snapshots.snapshot(case.store.database)
    with pytest.raises(ValueError, match="committed runtime"):
        case.store.record_interpretation(request)
    assert snapshots.snapshot(case.store.database) == before


def test_trace_cannot_be_omitted(tmp_path: Path) -> None:
    """Final facts alone are not complete interpretation evidence."""
    case = fixtures.installed(tmp_path)
    request = fixtures.proposal(case)
    before = snapshots.snapshot(case.store.database)
    request = request.model_copy(update={PROPOSAL: request.proposal.model_copy(
        update={"steps": ()},
    )})
    with pytest.raises(ValueError, match="untranslated raw output"):
        case.store.record_interpretation(request)
    assert snapshots.snapshot(case.store.database) == before
