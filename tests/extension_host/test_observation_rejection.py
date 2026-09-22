# Copyright (c) 2026 Zhambyl Yermagambet
"""Reject stale or invalid original input before it becomes pending work."""

from pathlib import Path

import pytest
from baqylau_extension_api.errors import ExtensionContractError

from extensions.models.lifecycle_state import ManagerClaim
from repository.errors import EventIdentityConflictError
from tests import sqlite_migration_fixture as snapshots
from tests.extension_api import source_samples
from tests.extension_host import observation_fixture as fixtures, observation_requests as requests

LATER_TIME = 2000.0


@pytest.mark.parametrize("field", ["manager_id", "runtime_revision", "extension_id"])
def test_wrong_runtime_identity_is_rejected(tmp_path: Path, field: str) -> None:
    """A caller cannot write for another owner or processing generation."""
    case = fixtures.installed(tmp_path)
    before = snapshots.snapshot(case.store.database)
    with pytest.raises(ValueError, match=r"committed runtime|not enabled"):
        case.store.append_observations(case.request.model_copy(update={field: "wrong"}))
    assert snapshots.snapshot(case.store.database) == before


def test_old_manager_cannot_append_after_claim(tmp_path: Path) -> None:
    """A new manager claim fences an old worker even before runtime restore."""
    case = fixtures.installed(tmp_path)
    state = case.lifecycle.read_extension_lifecycle()
    assert case.lifecycle.claim_extension_manager(ManagerClaim(
        expected_revision=state.revision, manager_id="new-manager", claimed_at=LATER_TIME,
    )).accepted
    with pytest.raises(ValueError, match="committed runtime"):
        case.store.append_observations(case.request)
    assert not case.store.pending_observations(10)


@pytest.mark.parametrize("encoded", ["42", '{"unexpected":true}', "not-json"])
def test_undeclared_document_shape_is_rejected(tmp_path: Path, encoded: str) -> None:
    """The retained source schema checks content before any raw row is written."""
    case = fixtures.installed(tmp_path)
    with pytest.raises(ExtensionContractError):
        case.store.append_observations(requests.document(case.request, encoded))
    assert not case.store.pending_observations(10)


def test_changed_original_bytes_are_rejected(tmp_path: Path) -> None:
    """Even a whitespace-only document change cannot replace the original bytes."""
    case = fixtures.installed(tmp_path)
    accepted = case.store.append_observations(case.request).accepted
    encoded = f"{source_samples.first_document(source_samples.batch())}\n"
    with pytest.raises(EventIdentityConflictError):
        case.store.append_observations(requests.document(case.request, encoded))
    assert case.store.pending_observations(10) == accepted


def test_late_conflict_rolls_back_earlier_input(tmp_path: Path) -> None:
    """A conflict in the second input cannot retain the first input or its cursor."""
    case = fixtures.installed(tmp_path)
    case.store.append_observations(case.request)
    before = snapshots.snapshot(case.store.database)
    changed = requests.document(case.request, '"changed"')
    request = case.request.model_copy(update={"observations": (
        *requests.new_key(case.request, "new").observations, *changed.observations,
    )})
    with pytest.raises(EventIdentityConflictError):
        case.store.append_observations(request)
    assert snapshots.snapshot(case.store.database) == before


def test_unknown_parent_rolls_back_whole_append(tmp_path: Path) -> None:
    """A source cannot add an audit link to missing input or a missing fact."""
    case = fixtures.installed(tmp_path)
    invalid = requests.causes(requests.new_key(case.request, "second"), ("missing-parent",))
    observations = (*case.request.observations, *invalid.observations)
    request = case.request.model_copy(update={"observations": observations})
    with pytest.raises(ValueError, match="cause is not recorded"):
        case.store.append_observations(request)
    assert not case.store.pending_observations(10)
