# Copyright (c) 2026 Zhambyl Yermagambet
"""Fence stopped manager generations and preserve interrupted operation evidence."""

from pathlib import Path

import pytest

from extensions.models.lifecycle_state import ManagerClaim
from tests.extension_host import lifecycle_fixture as fixtures


def test_new_manager_interrupts_pending_work(tmp_path: Path) -> None:
    """Restart does not silently complete preparation or accept its late result."""
    store = fixtures.claimed_repository(tmp_path)
    accepted = store.accept_extension_operation(fixtures.proposal(store), fixtures.NOW)
    assert accepted.operation is not None
    claim = ManagerClaim(expected_revision=accepted.state.revision, manager_id="new-manager", claimed_at=fixtures.NOW)
    claimed = fixtures.repository(tmp_path).claim_extension_manager(claim)
    assert claimed.accepted and claimed.state.pending_operation is None
    assert not store.finish_extension_operation(fixtures.completion(accepted.operation)).accepted
    operation = store.read_extension_operation(fixtures.OPERATION)
    assert operation is not None and operation.status == "interrupted"
    assert operation.failure is not None and operation.failure.code == "interrupted"


def test_recovery_retains_last_committed_set(tmp_path: Path) -> None:
    """Interrupted later preparation does not discard a prior accepted runtime."""
    store = fixtures.claimed_repository(tmp_path)
    original = fixtures.proposal(store, fixtures.install_package(tmp_path))
    fixtures.commit(store, original)
    accepted = store.accept_extension_operation(fixtures.proposal(store, operation_id="later"), fixtures.NOW)
    claimed = store.claim_extension_manager(ManagerClaim(
        expected_revision=accepted.state.revision, manager_id="replacement", claimed_at=fixtures.NOW,
    ))
    assert claimed.state.committed_runtime == original.candidate
    assert claimed.state.intents == original.intents


def test_runtime_ids_stay_reserved_after_restart(tmp_path: Path) -> None:
    """A failed or interrupted runtime ID cannot return and validate old handles."""
    store = fixtures.claimed_repository(tmp_path)
    first = fixtures.proposal(store)
    accepted = store.accept_extension_operation(first, fixtures.NOW)
    reopened = fixtures.repository(tmp_path)
    reopened.claim_extension_manager(ManagerClaim(
        expected_revision=accepted.state.revision, manager_id="replacement", claimed_at=fixtures.NOW,
    ))
    following = fixtures.proposal(reopened, operation_id="following")
    following = following.model_copy(update={"candidate": first.candidate})
    with pytest.raises(ValueError, match="unused runtime revision"):
        reopened.accept_extension_operation(following, fixtures.NOW)


def test_completion_retry_cannot_rewind(tmp_path: Path) -> None:
    """A duplicate completion is idempotent only at its unchanged completed head."""
    store = fixtures.claimed_repository(tmp_path)
    accepted = store.accept_extension_operation(fixtures.proposal(store), fixtures.NOW)
    assert accepted.operation is not None
    completed = fixtures.completion(accepted.operation)
    first = store.finish_extension_operation(completed)
    assert store.finish_extension_operation(completed).state == first.state
    store.accept_extension_operation(fixtures.proposal(store, operation_id="later"), fixtures.NOW)
    assert not store.finish_extension_operation(completed).accepted
