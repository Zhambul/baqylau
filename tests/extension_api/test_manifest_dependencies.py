# Copyright (c) 2026 Zhambyl Yermagambet
"""Verify compatible peer discovery and deterministic activation order."""

from itertools import permutations

import pytest
from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.manifest.activation import activation_order
from baqylau_extension_api.manifest.metadata import LoadOrder, PackageDependency
from baqylau_extension_api.manifest.validation import validate_manifest

from tests.extension_api import manifest_samples

OWNER = "test.owner"
PEER = "test.peer"
DEPENDENCIES = "dependencies"


def test_order_is_independent_of_discovery() -> None:
    """Apply dependency order first and extension ID among all ready packages."""
    first = manifest_samples.backend_manifest("test.a").model_copy(update={
        DEPENDENCIES: (PackageDependency(extension_id="test.b", version_range=">=1,<2"),),
    })
    second = manifest_samples.backend_manifest("test.b")
    last = manifest_samples.backend_manifest("test.z")
    for sequence in permutations((first, second, last)):
        assert activation_order(sequence) == ("test.b", "test.a", "test.z")


@pytest.mark.parametrize("required", [True, False])
def test_missing_peer_rules(*, required: bool) -> None:
    """Require hard dependencies but allow an absent optional peer."""
    manifest = manifest_samples.backend_manifest(OWNER).model_copy(update={
        DEPENDENCIES: (PackageDependency(extension_id=PEER, version_range=">=1", required=required),),
    })
    if required:
        with pytest.raises(ExtensionContractError, match="dependency"):
            activation_order((manifest,))
    else:
        assert activation_order((manifest,)) == (OWNER,)


def test_incompatible_optional_peer_order() -> None:
    """Do not expose an incompatible optional peer as a satisfied dependency."""
    manifest = manifest_samples.backend_manifest(OWNER).model_copy(update={
        DEPENDENCIES: (PackageDependency(extension_id=PEER, version_range=">=2", required=False),),
    })
    assert activation_order((manifest, manifest_samples.backend_manifest(PEER))) == (OWNER, PEER)


def test_required_peer_version_is_checked() -> None:
    """Reject a present dependency with an unsupported release."""
    manifest = manifest_samples.backend_manifest(OWNER).model_copy(update={
        DEPENDENCIES: (PackageDependency(extension_id=PEER, version_range=">=2"),),
    })
    with pytest.raises(ExtensionContractError, match="dependency"):
        activation_order((manifest, manifest_samples.backend_manifest(PEER)))


def test_order_cycle_is_rejected() -> None:
    """Combine required dependency and explicit ordering in the same graph."""
    manifest = manifest_samples.backend_manifest(OWNER).model_copy(update={
        DEPENDENCIES: (PackageDependency(extension_id=PEER, version_range=">=1"),),
        "load_order": LoadOrder(before=(PEER,)),
    })
    with pytest.raises(ExtensionContractError, match="cycle"):
        activation_order((manifest, manifest_samples.backend_manifest(PEER)))


@pytest.mark.parametrize("order", [
    LoadOrder(before=(OWNER,)), LoadOrder(before=(PEER,), after=(PEER,)),
])
def test_invalid_local_order_is_rejected(order: LoadOrder) -> None:
    """Reject self order and opposing constraints even when a peer is absent."""
    manifest = manifest_samples.backend_manifest(OWNER).model_copy(update={"load_order": order})
    with pytest.raises(ExtensionContractError):
        validate_manifest(manifest)


def test_duplicate_active_owners_are_rejected() -> None:
    """Do not choose an installed version by discovery order."""
    manifest = manifest_samples.backend_manifest()
    with pytest.raises(ExtensionContractError, match="active package IDs"):
        activation_order((manifest, manifest))
