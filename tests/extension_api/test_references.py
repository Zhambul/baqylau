# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep schema resolution inside the registered schema set."""

import pytest
from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.models.documents import EncodedDocument
from baqylau_extension_api.schemas import SchemaSet

from tests.extension_api import samples as fixtures

ENCODED_TEXT = '"hello"'


@pytest.mark.parametrize("reference", ["https://example.com/schema", "file:///etc/passwd", "../other.json"])
def test_schema_rejects_external_reference(reference: str) -> None:
    """Stop reference escapes before processing any extension document."""
    with pytest.raises(ExtensionContractError, match="outside the package"):
        SchemaSet((fixtures.schema_definition(f'{{"$ref":"{reference}"}}'),))


@pytest.mark.parametrize("encoded", [
    '{"$id":"https://example.com/schema"}',
    '{"properties":{"body":{"$id":"other"}}}',
    '{"$defs":{"body":{"$schema":"https://json-schema.org/draft-07/schema"}}}',
    '{"items":{"$dynamicRef":"https://example.com/schema#anchor"}}',
])
def test_schema_rejects_nested_escape(encoded: str) -> None:
    """Apply reference and dialect rules to actual nested schemas."""
    with pytest.raises(ExtensionContractError):
        SchemaSet((fixtures.schema_definition(encoded),))


def test_schema_preserves_default_data() -> None:
    """Permit literal reference-shaped data in defaults and examples."""
    definition = fixtures.schema_definition('{"type":"string","default":{"$id":"text","$ref":"text"}}')
    SchemaSet((definition,)).validate(EncodedDocument(schema_ref=definition.reference, json_text=ENCODED_TEXT))


def test_schema_resolves_local_definitions() -> None:
    """Resolve a local fragment without network access."""
    definition = fixtures.schema_definition('{"$defs":{"text":{"type":"string"}},"$ref":"#/$defs/text"}')
    SchemaSet((definition,)).validate(EncodedDocument(schema_ref=definition.reference, json_text=ENCODED_TEXT))


def test_schema_resolves_peer_schema() -> None:
    """Resolve a schema in the same validated schema set."""
    reference = f"urn:baqylau:{fixtures.EXTENSION_ID}:text:1"
    definition = fixtures.schema_definition(f'{{"$ref":"{reference}"}}', name="linked")
    SchemaSet((definition, fixtures.schema_definition())).validate(
        EncodedDocument(schema_ref=definition.reference, json_text=ENCODED_TEXT),
    )


def test_schema_rejects_missing_fragment() -> None:
    """Reject an unresolved local pointer before registration completes."""
    definition = fixtures.schema_definition('{"$ref":"#/$defs/missing"}')
    with pytest.raises(ExtensionContractError):
        SchemaSet((definition,))
