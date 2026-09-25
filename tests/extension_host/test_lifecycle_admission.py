# Copyright (c) 2026 Zhambyl Yermagambet
"""Reject stale and conflicting lifecycle admission without changing intent."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from tests import storage_reads
from tests.extension_host import lifecycle_fixture as fixtures


def test_replay_does_not_create_an_operation(tmp_path: Path) -> None:
    """An exact request retry returns its prior record without advancing the head."""
    store = fixtures.claimed_repository(tmp_path)
    proposed = fixtures.proposal(store)
    accepted = store.accept_extension_operation(proposed, fixtures.NOW)
    replayed = store.accept_extension_operation(proposed, fixtures.NOW + 1)
    assert replayed.status == "replayed"
    assert replayed.state == accepted.state and replayed.operation == accepted.operation


def test_request_id_cannot_name_different_work(tmp_path: Path) -> None:
    """Changed request bytes cannot reuse an existing operation identity."""
    store = fixtures.claimed_repository(tmp_path)
    proposed = fixtures.proposal(store)
    store.accept_extension_operation(proposed, fixtures.NOW)
    with pytest.raises(ValueError, match="different lifecycle request"):
        store.accept_extension_operation(proposed.model_copy(update={"kind": "failure"}), fixtures.NOW)


def test_pending_operation_reports_busy(tmp_path: Path) -> None:
    """A current writer sees busy while a different candidate is in preparation."""
    store = fixtures.claimed_repository(tmp_path)
    store.accept_extension_operation(fixtures.proposal(store), fixtures.NOW)
    following = fixtures.proposal(store, operation_id="following")
    assert store.accept_extension_operation(following, fixtures.NOW).status == "busy"
    assert storage_reads.read_extension_runtime(store, following.candidate.runtime_revision) is None


@pytest.mark.parametrize("change", ["manager", "revision", "catalog"])
def test_stale_admission_does_not_reserve(tmp_path: Path, change: str) -> None:
    """All host-selected revision checks occur inside the write transaction."""
    store = fixtures.claimed_repository(tmp_path)
    proposed = fixtures.proposal(store)
    if change == "manager":
        proposed = proposed.model_copy(update={"manager_id": "old"})
    if change == "revision":
        proposed = proposed.model_copy(update={"expected_revision": 0})
    if change == "catalog":
        candidate = proposed.candidate.model_copy(update={"catalog_revision": 1})
        proposed = proposed.model_copy(update={"candidate": candidate})
    assert store.accept_extension_operation(proposed, fixtures.NOW).status == "stale"
    assert store.read_extension_operation(proposed.operation_id) is None


def test_concurrent_admission_has_one_winner(tmp_path: Path) -> None:
    """Two repository objects cannot reserve separate candidates at the same head."""
    stores = (fixtures.claimed_repository(tmp_path), fixtures.repository(tmp_path))
    proposed = (
        fixtures.proposal(stores[0], operation_id="first"),
        fixtures.proposal(stores[1], operation_id="second"),
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = (
            pool.submit(stores[0].accept_extension_operation, proposed[0], fixtures.NOW),
            pool.submit(stores[1].accept_extension_operation, proposed[1], fixtures.NOW),
        )
        statuses = sorted(task.result(timeout=3).status for task in pending)
    assert statuses == ["accepted", "stale"]
