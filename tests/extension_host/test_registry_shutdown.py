# Copyright (c) 2026 Zhambyl Yermagambet
"""Stop new registry admissions before releasing the last active worker owner."""

from concurrent.futures import ThreadPoolExecutor

import pytest

from extensions.registry import ActiveExtensionRegistry
from extensions.registry_contract import RegistryClosedError
from tests.extension_host import registry_fixture, registry_memory_fixture

INITIAL = "initial"


def test_registry_close_retains_borrowed_read() -> None:
    """Closing admission does not release or invalidate a read already in progress."""
    registry = ActiveExtensionRegistry(INITIAL)
    with registry.read_snapshot() as selected:
        assert not registry.close_registry(0)
        assert selected.snapshot.directory.runtime_revision == INITIAL
        with pytest.raises(RegistryClosedError), registry.read_snapshot():
            pytest.fail("closed registry admitted a new reader")
    assert registry.close_registry(0)


def test_registry_close_waits_for_reader_release() -> None:
    """The final reader wakes a concurrent close without a polling loop."""
    registry = ActiveExtensionRegistry(INITIAL)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with registry.read_snapshot():
            pending = pool.submit(registry.close_registry, 3)
        assert pending.result(timeout=3)


def test_closed_registry_cannot_publish() -> None:
    """An accepted earlier candidate cannot reopen a stopped registry."""
    registry = ActiveExtensionRegistry(INITIAL)
    assert registry.close_registry(0)
    with pytest.raises(RegistryClosedError):
        registry.publish_snapshot(0, registry_fixture.snapshot(), registry_memory_fixture.MEMORY_COMMIT)


@pytest.mark.parametrize("timeout", [-1, float("inf"), float("nan")])
def test_registry_close_requires_finite_bound(timeout: float) -> None:
    """Invalid bounds fail before admission is stopped."""
    registry = ActiveExtensionRegistry(INITIAL)
    with pytest.raises(ValueError, match="finite non-negative"):
        registry.close_registry(timeout)
    with registry.read_snapshot() as selected:
        assert selected.revision == 0
