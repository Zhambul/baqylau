# Copyright (c) 2026 Zhambyl Yermagambet
"""Validate optional absence and complete required-dependent removal plans."""

from dataclasses import replace

import pytest
from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.manifest.metadata import PackageDependency

from extensions.registry_removal import removal_order
from tests.extension_api import manifest_samples, service_samples as peers
from tests.extension_host import registry_fixture as fixtures

FIRST = "test.first"


def test_transitive_required_removal() -> None:
    """Remove consumers before providers, including indirect required dependencies."""
    first = manifest_samples.backend_manifest(FIRST)
    second = first.model_copy(update={"extension_id": "test.second", "dependencies": (
        PackageDependency(extension_id=FIRST, version_range=">=1", required=True),
    )})
    third = first.model_copy(update={"extension_id": "test.third", "dependencies": (
        PackageDependency(extension_id="test.second", version_range=">=1", required=True),
    )})
    assert removal_order((third, first, second), (FIRST,)) == ("test.third", "test.second", FIRST)


def test_optional_consumer_remains() -> None:
    """An optional peer does not require the consumer to stop."""
    manifests = (peers.manifest(peers.ALPHA), peers.manifest(peers.BETA))
    assert removal_order(manifests, (peers.BETA,)) == (peers.BETA,)
    assert fixtures.snapshot(fixtures.peer(peers.ALPHA)).active_order == (peers.ALPHA,)


def test_required_provider_cannot_be_absent() -> None:
    """An invalid active subset cannot be published after a provider removal."""
    caller = fixtures.peer(peers.ALPHA)
    required = caller.manifest.dependencies[0].model_copy(update={"required": True})
    manifest = caller.manifest.model_copy(update={"dependencies": (required,)})
    with pytest.raises(ExtensionContractError, match="required extension dependency"):
        fixtures.snapshot(replace(caller, manifest=manifest))


def test_active_dependency_cycle_is_rejected() -> None:
    """Both optional peers present still need a valid dependency order."""
    caller = fixtures.peer(peers.BETA)
    manifest = peers.manifest(peers.BETA, cycle=True)
    with pytest.raises(ExtensionContractError, match="cycle"):
        fixtures.snapshot(fixtures.peer(peers.ALPHA), replace(caller, manifest=manifest))


@pytest.mark.parametrize("owners", [("absent",), (peers.BETA, peers.BETA)])
def test_removal_requires_unique_active_owners(owners: tuple[str, ...]) -> None:
    """Invalid selection must not silently change a different set."""
    with pytest.raises(ValueError, match="unique active"):
        removal_order((peers.manifest(peers.BETA),), owners)
