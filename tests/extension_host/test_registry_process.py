# Copyright (c) 2026 Zhambyl Yermagambet
"""Run independent private workers through the checked active registry."""

import sys
from contextlib import ExitStack
from pathlib import Path

import pytest
from baqylau_extension_api.runtime.call_grants import HostCallLedger
from pydantic import TypeAdapter

from extensions.registry_package import RegistryPackage
from tests.extension_api import service_samples as peers
from tests.extension_host import registry_fixture as fixtures, registry_process_fixture as processes
from tests.extension_host.registry_memory_fixture import MemoryRegistry


@pytest.mark.parametrize("reverse", [False, True])
def test_private_peers_use_registry(
    tmp_path: Path, runtime_wheels: Path, *, reverse: bool,
) -> None:
    """Both preparation orders use declared public reads after atomic publication."""
    host = processes.RegistryWorkerHost(tmp_path, MemoryRegistry("initial"), HostCallLedger())
    owners = (peers.BETA, peers.ALPHA) if reverse else (peers.ALPHA, peers.BETA)
    with ExitStack() as cleanup:
        packages = tuple(host.prepare_peer(owner, runtime_wheels, cleanup) for owner in owners)
        assert host.registry.publish_snapshot(0, fixtures.snapshot(*packages)).status == "accepted"
        _read_alpha(host, "peer", peers.PEER_VALUE)
        _read_alpha(host, "factory", "not_installed")
        assert host.registry.publish_snapshot(1, fixtures.snapshot(revision="removed")).status == "accepted"
    assert "peer_backend" not in sys.modules


def test_private_consumer_can_run_alone(tmp_path: Path, runtime_wheels: Path) -> None:
    """An optional absent peer does not prevent the consumer's base read path."""
    host = processes.RegistryWorkerHost(tmp_path, MemoryRegistry("initial"), HostCallLedger())
    with ExitStack() as cleanup:
        package = host.prepare_peer(peers.ALPHA, runtime_wheels, cleanup)
        host.registry.publish_snapshot(0, fixtures.snapshot(package))
        _read_alpha(host, "peer", "not_installed")
        host.registry.publish_snapshot(1, fixtures.snapshot(revision="removed"))
    assert not tuple((tmp_path / peers.ALPHA / "environments").iterdir())


def _read_alpha(host: processes.RegistryWorkerHost, mode: str, expected: str) -> None:
    with host.registry.read_snapshot() as selected:
        caller = next(
            package for package in selected.snapshot.packages if package.manifest.extension_id == peers.ALPHA
        )
        _read_package(host.calls, caller, mode, expected)


def _read_package(ledger: HostCallLedger, caller: RegistryPackage, mode: str, expected: str) -> None:
    assert caller.environment is not None and caller.plugin is not None
    queries = caller.plugin.capabilities.queries
    assert queries is not None
    request = peers.query_request(peers.ALPHA, mode)
    with ledger.root(caller.environment, request.binding.scope, 5):
        response = queries.query(request)
    assert response.status == "ready"
    assert TypeAdapter(str).validate_json(response.document.json_text) == expected
