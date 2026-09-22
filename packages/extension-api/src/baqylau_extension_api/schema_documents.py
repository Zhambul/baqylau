# Copyright (c) 2026 Zhambyl Yermagambet
"""Decode schema documents only at the schema validation boundary."""

import hashlib
from collections.abc import Iterator

from pydantic import ConfigDict, JsonValue, TypeAdapter, ValidationError
from referencing.jsonschema import DRAFT202012, Schema

from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.models.documents import SchemaDefinition, SchemaIdentity, SchemaRef

JSON_DOCUMENT: TypeAdapter[JsonValue] = TypeAdapter(JsonValue, config=ConfigDict(allow_inf_nan=False))
SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"


def export_schema[Document](adapter: TypeAdapter[Document], identity: SchemaIdentity) -> SchemaDefinition:
    """Export a typed public contract as a content-addressed schema.

    Returns:
        A definition that can be persisted without its Python package.

    """
    encoded = JSON_DOCUMENT.dump_json(adapter.json_schema(mode="validation")).decode("utf-8")
    return SchemaDefinition(
        reference=SchemaRef(
            owner=identity.owner, name=identity.name, version=identity.version,
            digest=hashlib.sha256(encoded.encode()).hexdigest(),
        ),
        json_text=encoded,
    )


def schema_uri(definition: SchemaDefinition) -> str:
    """Return the immutable local identifier for a bundled schema.

    Returns:
        A URI that does not need a network request.

    """
    reference = definition.reference
    return f"urn:baqylau:{reference.owner}:{reference.name}:{reference.version}"


def schema_document(definition: SchemaDefinition) -> dict[str, JsonValue]:
    """Decode a schema after checking its recorded digest.

    Returns:
        The declared JSON schema object.

    Raises:
        ExtensionContractError: If the identity or dialect is invalid.

    """
    digest = hashlib.sha256(definition.json_text.encode()).hexdigest()
    if digest != definition.reference.digest:
        message = "schema digest does not match its content"
        raise ExtensionContractError(message)
    document = decode_json(definition.json_text)
    if not isinstance(document, dict) or document.get("$schema", SCHEMA_DIALECT) != SCHEMA_DIALECT:
        message = "schema must be a JSON Schema 2020-12 object"
        raise ExtensionContractError(message)
    return document


def decode_json(encoded: str) -> JsonValue:
    """Decode JSON at the schema boundary with one error type.

    Returns:
        The decoded JSON document.

    Raises:
        ExtensionContractError: If the document is not valid finite JSON.

    """
    try:
        return JSON_DOCUMENT.validate_python(
            JSON_DOCUMENT.validate_json(encoded, strict=True), strict=True,
        )
    except ValidationError as error:
        message = "document is not valid finite JSON"
        raise ExtensionContractError(message) from error


def external_references(definition: SchemaDefinition) -> tuple[str, ...]:
    """Read external schema targets without exposing decoded schema objects.

    Returns:
        Referenced schema URIs, excluding local fragment references.

    """
    targets = (
        reference.partition("#")[0]
        for reference in references(schema_document(definition))
    )
    return tuple(target for target in targets if target)


def references(document: Schema) -> Iterator[str]:
    """Read reference and identifier values in a schema document.

    Yields:
        Each reference in a schema, not in annotation or default data.

    Raises:
        ExtensionContractError: If a schema sets its own identifier or dialect.

    """
    if isinstance(document, bool):
        return
    if "$id" in document:
        message = "schema identifiers are assigned by the host"
        raise ExtensionContractError(message)
    if document.get("$schema", SCHEMA_DIALECT) != SCHEMA_DIALECT:
        message = "nested schemas must use JSON Schema 2020-12"
        raise ExtensionContractError(message)
    for name in ("$ref", "$dynamicRef"):
        reference = document.get(name)
        if isinstance(reference, str):
            yield reference
    for child in DRAFT202012.subresources_of(document):
        yield from references(child)
