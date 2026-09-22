# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep ordering, installed state, and worker identity in one checked selection."""

from dataclasses import replace

import pytest
from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.models.directory import DirectoryRequest
from baqylau_extension_api.models.scopes import InstallationScope

from tests.extension_api import manifest_samples, service_samples as peers
from tests.extension_host import registry_fixture as fixtures

PEER_COUNT = 2


@pytest.mark.parametrize("reverse", [False, True])
def test_registry_has_stable_active_order(*, reverse: bool) -> None:
    """Input order cannot alter dependencies or public metadata order."""
    packages = (fixtures.peer(peers.ALPHA), fixtures.peer(peers.BETA))
    snapshot = fixtures.snapshot(*(reversed(packages) if reverse else packages))
    assert snapshot.active_order == (peers.BETA, peers.ALPHA)
    owners = tuple(entry.extension_info.extension_id for entry in snapshot.directory.entries)
    assert owners == (peers.ALPHA, peers.BETA)


def test_registry_keeps_inactive_metadata() -> None:
    """Disabled packages are installed, but their public service is not active."""
    disabled = fixtures.inactive(fixtures.peer(peers.BETA))
    snapshot = fixtures.snapshot(fixtures.peer(peers.ALPHA), disabled)
    provider = snapshot.get_service_provider(peers.BETA, InstallationScope())
    assert provider is not None
    assert provider.environment is None and provider.queries is None
    assert len(snapshot.list_extensions(DirectoryRequest(active_only=True)).entries) == 1
    assert len(snapshot.list_extensions(DirectoryRequest()).entries) == PEER_COUNT
    assert snapshot.get_service_provider("absent", InstallationScope()) is None


def test_registry_accepts_web_only_package() -> None:
    """An enabled web-only package needs identity, but no Python worker."""
    package = fixtures.peer(peers.BETA)
    manifest = manifest_samples.web_manifest(peers.BETA)
    snapshot = fixtures.snapshot(replace(package, manifest=manifest, plugin=None))
    assert snapshot.active_order == (peers.BETA,)


@pytest.mark.parametrize("change", ["identity", "environment", "plugin", "inactive", "backend"])
def test_registry_rejects_inconsistent_runtime(change: str) -> None:
    """No mismatched or inactive worker can enter a prepared selection."""
    package = fixtures.peer(peers.BETA)
    if change == "identity":
        package = replace(package, entry=fixtures.peer(peers.ALPHA).entry)
    if change == "environment":
        package = replace(package, environment=fixtures.peer(peers.BETA, "old").environment)
    if change == "plugin":
        package = replace(package, plugin=fixtures.peer(peers.ALPHA).plugin)
    if change == "inactive":
        package = replace(package, entry=fixtures.inactive(package).entry)
    if change == "backend":
        package = replace(package, plugin=None)
    with pytest.raises(ExtensionContractError):
        fixtures.snapshot(package)


def test_registry_rejects_duplicate_owner() -> None:
    """Duplicate installed IDs cannot hide behind a stable sort."""
    package = fixtures.peer(peers.BETA)
    with pytest.raises(ValueError, match="IDs must be unique"):
        fixtures.snapshot(package, package)
