# Copyright (c) 2026 Zhambyl Yermagambet
"""Validate independent owned feed declarations without feature imports."""

import pytest
from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.manifest.validation import validate_manifest

from tests.extension_api import projection_samples, samples


def test_projection_manifest_registers_feed_types() -> None:
    """Keep derived feed schemas separate from canonical event schemas."""
    manifest = projection_samples.manifest()
    assert validate_manifest(manifest) == manifest
    assert manifest.contributions.entry_types[0].name != manifest.contributions.event_types[0].name


@pytest.mark.parametrize("change", [
    {"name": "peer.card"}, {"schema_ref": samples.encoded_document().schema_ref}, {"scopes": ("session", "session")},
])
def test_entry_registration_requires_owner(change: dict[str, object]) -> None:
    """Require unique scopes, an owned namespace, and an owned schema."""
    manifest = projection_samples.manifest()
    entry = manifest.contributions.entry_types[0].model_copy(update=change)
    contributions = manifest.contributions.model_copy(update={"entry_types": (entry,)})
    with pytest.raises(ExtensionContractError):
        validate_manifest(manifest.model_copy(update={"contributions": contributions}))


def test_entry_registration_rejects_duplicate_ids() -> None:
    """Do not select a feed schema by manifest order when names conflict."""
    manifest = projection_samples.manifest()
    contributions = manifest.contributions.model_copy(update={"entry_types": manifest.contributions.entry_types * 2})
    with pytest.raises(ExtensionContractError, match="unique"):
        validate_manifest(manifest.model_copy(update={"contributions": contributions}))
