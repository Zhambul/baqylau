# Copyright (c) 2026 Zhambyl Yermagambet
"""Reject invalid schema reference targets without fetching remote data."""

import pytest
from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.models.documents import EncodedDocument
from baqylau_extension_api.schemas import SchemaSet

from tests.extension_api import samples


@pytest.mark.parametrize("encoded", [
    '{"title":"not a schema","$ref":"#/title"}',
    '{"$defs":{"text":{"type":"string"}},"$ref":"#missing"}',
])
def test_reference_must_resolve_to_schema(encoded: str) -> None:
    """Fail registration for missing anchors and non-schema pointer targets."""
    with pytest.raises(ExtensionContractError, match="does not resolve"):
        SchemaSet((samples.schema_definition(encoded),))


def test_named_anchor_resolves_in_same_schema() -> None:
    """Allow a local named anchor in the selected schema dialect."""
    definition = samples.schema_definition('{"$defs":{"text":{"$anchor":"text","type":"string"}},"$ref":"#text"}')
    SchemaSet((definition,)).validate(EncodedDocument(schema_ref=definition.reference, json_text='"text"'))


def test_recursive_failure_stays_at_boundary() -> None:
    """Convert unbounded reference evaluation to a contract error."""
    definition = samples.schema_definition('{"$ref":"#"}')
    with pytest.raises(ExtensionContractError):
        SchemaSet((definition,)).validate(EncodedDocument(schema_ref=definition.reference, json_text='"text"'))
