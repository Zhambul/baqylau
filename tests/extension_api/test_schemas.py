# Copyright (c) 2026 Zhambyl Yermagambet
"""Check schema ownership, local resolution, and document validation."""

import pytest
from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.models.documents import EncodedDocument, SchemaDefinition, SchemaRef
from baqylau_extension_api.schemas import SchemaSet

from tests.extension_api import samples as fixtures


def test_schema_preserves_source_bytes() -> None:
    """Keep the stored document bytes after successful validation."""
    document = fixtures.encoded_document('  "hello"\n')
    SchemaSet((fixtures.schema_definition(),)).validate(document)
    assert document.json_text == '  "hello"\n'


@pytest.mark.parametrize("encoded", ["123", '"unfinished', "NaN", "Infinity", "null"])
def test_schema_rejects_invalid_documents(encoded: str) -> None:
    """Report one contract error for malformed and schema-invalid data."""
    with pytest.raises(ExtensionContractError):
        SchemaSet((fixtures.schema_definition(),)).validate(fixtures.encoded_document(encoded))


def test_schema_rejects_wrong_digest() -> None:
    """Check exact source bytes before registering a schema."""
    invalid = SchemaDefinition(reference=fixtures.schema_definition().reference, json_text='{"type":"integer"}')
    with pytest.raises(ExtensionContractError, match="digest"):
        SchemaSet((invalid,))


def test_schema_rejects_duplicate_registration() -> None:
    """Do not let the same schema identity replace another definition."""
    with pytest.raises(ExtensionContractError, match="unique"):
        SchemaSet((fixtures.schema_definition(), fixtures.schema_definition('{"type":"integer"}')))


@pytest.mark.parametrize("encoded", [
    "[]", "true", '{"type":"not-a-type"}', '{"$schema":"https://json-schema.org/draft-07/schema"}', "{",
])
def test_schema_rejects_invalid_definition(encoded: str) -> None:
    """Require a valid schema object in the declared dialect."""
    with pytest.raises(ExtensionContractError):
        SchemaSet((fixtures.schema_definition(encoded),))


def test_document_requires_exact_schema() -> None:
    """Reject a document whose schema name matches but digest does not."""
    unknown = SchemaRef(owner=fixtures.EXTENSION_ID, name="text", version=1, digest=fixtures.DIGEST)
    document = EncodedDocument(schema_ref=unknown, json_text='"hello"')
    with pytest.raises(ExtensionContractError, match="not registered"):
        SchemaSet((fixtures.schema_definition(),)).validate(document)


def test_schema_loads_without_extension_code() -> None:
    """Validate data from persisted schema text after package removal."""
    stored = fixtures.schema_definition().model_dump_json()
    SchemaSet((SchemaDefinition.model_validate_json(stored),)).validate(fixtures.encoded_document())
