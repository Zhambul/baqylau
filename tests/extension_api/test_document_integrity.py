# Copyright (c) 2026 Zhambyl Yermagambet
"""Reject invalid encoded documents even after unchecked model copies."""

import pytest
from baqylau_extension_api.core.content import StructuredContent
from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.models.documents import MAX_DOCUMENT_CHARACTERS, EncodedDocument
from baqylau_extension_api.schemas import SchemaSet
from pydantic import ValidationError

from tests.extension_api import samples


@pytest.mark.parametrize("encoded", ["", "not json", '{"broken":', "NaN", "Infinity", '{"number":1e999}'])
def test_core_structured_content_is_valid_json(encoded: str) -> None:
    """Reject invalid or non-finite JSON before it reaches a private core mapper."""
    with pytest.raises(ValidationError):
        StructuredContent(json_text=encoded)


def test_core_content_preserves_valid_encoding() -> None:
    """Do not rewrite an external document while checking its public boundary."""
    encoded = '{ "message": "Жамбыл", "number": 1.00 }'
    assert StructuredContent(json_text=encoded).json_text == encoded


def test_registry_rechecks_schema_copies() -> None:
    """Reject a forged model whose size bypassed normal construction."""
    oversized = " " * (MAX_DOCUMENT_CHARACTERS + 1)
    invalid = samples.schema_definition().model_copy(update={"json_text": oversized})
    with pytest.raises(ValidationError, match="at most"):
        SchemaSet((invalid,))


def test_registry_rechecks_document_copies() -> None:
    """Apply model size limits as well as the selected document schema."""
    oversized = " " * (MAX_DOCUMENT_CHARACTERS + 1)
    invalid = samples.encoded_document().model_copy(update={"json_text": oversized})
    with pytest.raises(ValidationError, match="at most"):
        SchemaSet((samples.schema_definition(),)).validate(invalid)


@pytest.mark.parametrize("encoded", ["NaN", "Infinity", "-Infinity", '{"number":1e999}', "[1, [NaN]]"])
def test_schema_never_accepts_nonfinite_json(encoded: str) -> None:
    """Reject non-finite values even when the selected schema permits all data."""
    schema = samples.schema_definition("{}")
    document = EncodedDocument(schema_ref=schema.reference, json_text=encoded)
    with pytest.raises(ExtensionContractError, match="finite JSON"):
        SchemaSet((schema,)).validate(document)
