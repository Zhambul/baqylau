# Copyright (c) 2026 Zhambyl Yermagambet
"""Check schema relationships across declared package dependencies."""

import pytest
from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.manifest.activation import activation_order
from baqylau_extension_api.manifest.metadata import PackageDependency
from baqylau_extension_api.models.documents import SchemaDefinition

from tests.extension_api import manifest_samples, samples

PEER_ID = "test.peer"


@pytest.mark.parametrize("declared", [True, False])
def test_schema_reference_requires_peer(*, declared: bool) -> None:
    """Do not accept an undeclared peer just because another package loaded it."""
    schema = samples.schema_definition()
    peer = manifest_samples.backend_manifest(PEER_ID).model_copy(update={"schemas": (SchemaDefinition(
        reference=schema.reference.model_copy(update={"owner": PEER_ID}), json_text=schema.json_text,
    ),)})
    manifest = manifest_samples.backend_manifest().model_copy(update={
        "schemas": (samples.schema_definition('{"$ref":"urn:baqylau:test.peer:text:1"}'),),
        "dependencies": (PackageDependency(extension_id=PEER_ID, version_range=">=1"),) if declared else (),
    })
    if declared:
        assert activation_order((manifest, peer)) == (PEER_ID, samples.EXTENSION_ID)
    else:
        with pytest.raises(ExtensionContractError, match="schema references require"):
            activation_order((manifest, peer))
