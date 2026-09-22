# Copyright (c) 2026 Zhambyl Yermagambet
"""Test compare-and-set publication and explicit reader ownership."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from tests.extension_api import samples, service_samples as peers
from tests.extension_host import registry_fixture as fixtures
from tests.extension_host.registry_memory_fixture import MemoryRegistry

INITIAL = "initial"
ACCEPTED = "accepted"


def test_publication_waits_for_all_readers() -> None:
    """Readers do not hold the mutex, and publication never waits on their calls."""
    registry = MemoryRegistry(INITIAL)
    candidate = fixtures.snapshot(fixtures.peer(peers.BETA))
    with ThreadPoolExecutor(max_workers=1) as pool, registry.read_snapshot() as selected:
        with registry.read_snapshot():
            pending = pool.submit(registry.publish_snapshot, 0, candidate)
            assert pending.result(timeout=3).status == "busy"
        assert registry.publish_snapshot(0, candidate).status == "busy"
        assert selected.revision == 0 and not selected.snapshot.active_order
    assert registry.publish_snapshot(0, candidate).status == ACCEPTED
    assert registry.publish_snapshot(0, candidate).status == "stale"


def test_failed_reader_releases_boundary() -> None:
    """A failed consumer cannot leave a permanent active read count.

    Raises:
        RuntimeError: To test context cleanup during a consumer failure.

    """
    registry = MemoryRegistry(INITIAL)
    message = "failed read"
    with pytest.raises(RuntimeError, match="failed read"), registry.read_snapshot():
        raise RuntimeError(message)
    assert registry.publish_snapshot(0, fixtures.snapshot()).status == ACCEPTED


def test_concurrent_publication_has_one_winner() -> None:
    """Two prepared selections cannot both replace the same head."""
    registry = MemoryRegistry(INITIAL)
    candidates = (fixtures.snapshot(revision="a"), fixtures.snapshot(revision="b"))
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = tuple(
            pool.submit(registry.publish_snapshot, 0, proposed) for proposed in candidates
        )
        statuses = tuple(task.result(timeout=3).status for task in pending)
    assert sorted(statuses) == [ACCEPTED, "stale"]


def test_reused_runtime_and_older_catalog() -> None:
    """Runtime IDs cannot return later and make old service references current again."""
    registry = MemoryRegistry(INITIAL)
    assert registry.publish_snapshot(0, fixtures.snapshot()).status == ACCEPTED
    with pytest.raises(ValueError, match="unused runtime"):
        registry.publish_snapshot(1, fixtures.snapshot(revision=INITIAL))
    proposed = fixtures.snapshot(revision="next")
    directory = proposed.directory.model_copy(update={"catalog_revision": 0})
    with pytest.raises(ValueError, match="backwards"):
        registry.publish_snapshot(1, replace(proposed, directory=directory))
    with registry.read_snapshot() as selected:
        assert selected.snapshot.directory.runtime_revision == samples.RUNTIME_REVISION


def test_publication_rechecks_snapshot() -> None:
    """A forged order cannot bypass the checked active plan."""
    registry = MemoryRegistry(INITIAL)
    proposed = fixtures.snapshot(fixtures.peer(peers.BETA))
    assert registry.publish_snapshot(0, replace(proposed, active_order=("forged",))).status == ACCEPTED
    with registry.read_snapshot() as selected:
        assert selected.snapshot.active_order == (peers.BETA,)
