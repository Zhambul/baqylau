# Copyright (c) 2026 Zhambyl Yermagambet
"""Check declared peer reads against real registry publication boundaries."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from typing import TYPE_CHECKING

import pytest
from baqylau_extension_api.errors import ExtensionContractError

from tests.extension_api import service_samples as peers
from tests.extension_host import registry_call_fixture as calls, registry_fixture as fixtures
from tests.extension_host.registry_memory_fixture import MemoryRegistry

if TYPE_CHECKING:
    from extensions.registry_package import RegistryPackage

INITIAL = "initial"
NEXT = "next"


def test_peer_query_holds_registry() -> None:
    """Provider lookup alone must not release a worker still executing a query."""
    registry = MemoryRegistry(INITIAL)
    held = calls.HeldQueries()
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(calls.authorized_read, calls.held_access(registry, held))
        with ExitStack() as cleanup:
            cleanup.callback(held.released.set)
            assert held.started.wait(3)
            assert registry.publish_snapshot(1, fixtures.snapshot(revision=NEXT)).status == "busy"
        assert pending.result(timeout=3) == "available"
    assert registry.publish_snapshot(1, fixtures.snapshot(revision=NEXT)).status == "accepted"


@pytest.mark.parametrize("present", [False, True])
def test_optional_service_unavailability(*, present: bool) -> None:
    """An absent package and an installed disabled package remain distinct."""
    registry = MemoryRegistry(INITIAL)
    caller = fixtures.peer(peers.ALPHA)
    packages: tuple[RegistryPackage, ...] = (caller,)
    if present:
        packages = (*packages, fixtures.inactive(fixtures.peer(peers.BETA)))
    registry.publish_snapshot(0, fixtures.snapshot(*packages))
    response = fixtures.service_access(registry, caller).resolve_service(peers.resolve_request())
    assert response.status == "unavailable"
    expected = "not_enabled" if present else "not_installed"
    assert response.reason == expected


def test_saved_callback_rejects_removed_caller() -> None:
    """A prior connection cannot query a provider after its owner leaves the set."""
    registry = MemoryRegistry(INITIAL)
    caller = fixtures.peer(peers.ALPHA)
    registry.publish_snapshot(0, fixtures.snapshot(caller, fixtures.peer(peers.BETA)))
    access = fixtures.service_access(registry, caller)
    replacement = fixtures.snapshot(fixtures.peer(peers.BETA, NEXT), revision=NEXT)
    registry.publish_snapshot(1, replacement)
    with pytest.raises(ExtensionContractError, match="not in the active registry"):
        calls.authorized_read(access)


def test_service_query_requires_authority() -> None:
    """A live caller and installed service do not authorize an ungranted callback."""
    registry = MemoryRegistry(INITIAL)
    caller = fixtures.peer(peers.ALPHA)
    registry.publish_snapshot(0, fixtures.snapshot(caller, fixtures.peer(peers.BETA)))
    with pytest.raises(ExtensionContractError):
        fixtures.service_access(registry, caller).query_service(peers.service_query())
    assert registry.publish_snapshot(1, fixtures.snapshot(revision=NEXT)).status == "accepted"
