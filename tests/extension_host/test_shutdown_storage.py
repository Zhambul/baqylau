# Copyright (c) 2026 Zhambyl Yermagambet
"""Retain exact shutdown observations across later manager claims and database reopen."""

from pathlib import Path

import pytest

from extensions.models.cleanup import RetirementIssue, RuntimeShutdown, ShutdownRecord
from extensions.models.lifecycle_state import ManagerClaim
from tests.extension_host import lifecycle_fixture as fixtures, shutdown_storage_fixture as storage


def test_first_shutdown_body_is_retained(tmp_path: Path) -> None:
    """A later clean stop cannot erase the earlier failed deactivation record."""
    store = fixtures.claimed_repository(tmp_path)
    original = _record()
    assert store.record_extension_shutdown(original)
    assert store.record_extension_shutdown(original)
    changed = original.model_copy(update={"runtimes": ()})
    with pytest.raises(ValueError, match="different body"):
        store.record_extension_shutdown(changed)
    assert fixtures.repository(tmp_path).read_extension_lifecycle().last_shutdown == original
    store.record_extension_shutdown(changed.model_copy(update={"record_id": "clean-stop"}))
    assert storage.records(store) == (
        original, changed.model_copy(update={"record_id": "clean-stop"}),
    )


def test_fenced_manager_can_record_shutdown(tmp_path: Path) -> None:
    """Old cleanup is history, not a new claim that the old runtime is active."""
    store = fixtures.claimed_repository(tmp_path)
    fixtures.commit(store, fixtures.proposal(store))
    previous = store.read_extension_lifecycle()
    store.claim_extension_manager(ManagerClaim(
        manager_id="next-manager", expected_revision=previous.revision, claimed_at=fixtures.NOW,
    ))
    assert store.record_extension_shutdown(_record())
    current = fixtures.repository(tmp_path).read_extension_lifecycle()
    assert current.manager_id == "next-manager"
    assert current.committed_runtime == previous.committed_runtime and current.intents == previous.intents
    assert current.last_shutdown == _record()


def test_unknown_shutdown_manager_is_rejected(tmp_path: Path) -> None:
    """An observation needs a known manager identity, not an invented runtime owner."""
    store = fixtures.claimed_repository(tmp_path)
    assert not store.record_extension_shutdown(_record().model_copy(update={"manager_id": "unknown"}))
    assert store.read_extension_lifecycle().last_shutdown is None


def _record() -> ShutdownRecord:
    return ShutdownRecord(
        record_id="failed-stop", manager_id=fixtures.MANAGER, recorded_at=fixtures.NOW,
        runtimes=(RuntimeShutdown(runtime_revision="runtime-one", resources_closed=True, issues=(RetirementIssue(
            runtime_revision="runtime-one", extension_id="test.sample", reason="unresolved_jobs",
            pending_job_ids=("external-write",),
        ),)),),
    )
