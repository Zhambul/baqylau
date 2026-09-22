# Copyright (c) 2026 Zhambyl Yermagambet
"""Retain requested state and immutable accepted runtime history across reopen."""

from pathlib import Path

from extensions.models.lifecycle_operations import LifecycleOperation
from extensions.models.lifecycle_selection import RuntimeSelection
from extensions.models.lifecycle_state import ManagerClaim
from tests.extension_host import lifecycle_fixture as fixtures, lifecycle_settings_fixture as settings


def test_initial_state_and_manager_claim(tmp_path: Path) -> None:
    """A new store has no running-state claim and no implicit enabled package."""
    store = fixtures.repository(tmp_path)
    assert store.read_extension_lifecycle().manager_id is None
    claim = ManagerClaim(expected_revision=0, manager_id=fixtures.MANAGER, claimed_at=fixtures.NOW)
    accepted = store.claim_extension_manager(claim)
    assert accepted.accepted and accepted.state.revision == 1
    assert not store.claim_extension_manager(claim).accepted
    repeated = claim.model_copy(update={"expected_revision": 1})
    assert store.claim_extension_manager(repeated).state == accepted.state
    assert fixtures.repository(tmp_path).read_extension_lifecycle() == accepted.state


def test_admission_keeps_commit_separate(tmp_path: Path) -> None:
    """Requested enable is visible while the new worker has not yet succeeded."""
    store = fixtures.claimed_repository(tmp_path)
    proposed = fixtures.proposal(store, fixtures.install_package(tmp_path))
    accepted = store.accept_extension_operation(proposed, fixtures.NOW)
    assert accepted.status == "accepted" and accepted.operation is not None
    assert accepted.state.committed_runtime is None
    assert accepted.state.pending_operation == proposed.operation_id
    assert accepted.state.intents == proposed.intents
    assert store.read_extension_runtime(proposed.candidate.runtime_revision) == proposed.candidate


def test_success_survives_reopen(tmp_path: Path) -> None:
    """One commit preserves the exact ordered candidate and its operation history."""
    store = fixtures.claimed_repository(tmp_path)
    proposed = fixtures.proposal(store, fixtures.install_package(tmp_path))
    fixtures.commit(store, proposed)
    reopened = fixtures.repository(tmp_path)
    state = reopened.read_extension_lifecycle()
    assert state.committed_runtime == proposed.candidate
    assert state.pending_operation is None
    operation = reopened.read_extension_operation(proposed.operation_id)
    assert operation is not None and operation.status == "succeeded"
    assert reopened.read_extension_operation("unknown") is None
    assert reopened.read_extension_runtime("unknown") is None


def test_failed_reload_keeps_committed_runtime(tmp_path: Path) -> None:
    """A failed prepared replacement cannot change the accepted runtime head."""
    store = fixtures.claimed_repository(tmp_path)
    first = fixtures.proposal(store, fixtures.install_package(tmp_path))
    fixtures.commit(store, first)
    assert isinstance(first.candidate, RuntimeSelection)
    replacement = fixtures.proposal(store, first.candidate.packages[0], "reload")
    admitted = store.accept_extension_operation(replacement, fixtures.NOW)
    assert admitted.operation is not None
    finished = settings.fail_preparation(store, admitted.operation)
    assert finished.accepted and finished.state.committed_runtime == first.candidate
    assert finished.state.pending_operation is None
    _assert_failed(store.read_extension_operation(replacement.operation_id))
    assert store.read_extension_runtime(replacement.candidate.runtime_revision) == replacement.candidate


def _assert_failed(operation: LifecycleOperation | None) -> None:
    assert operation is not None and operation.status == "failed"
    assert operation.failure is not None and operation.failure.code == "preparation_failed"
