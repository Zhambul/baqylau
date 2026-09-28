# Copyright (c) 2026 Zhambyl Yermagambet
"""Validate extension documents against an immutable local schema set.

A schema set is compiled once for each distinct tuple of definitions. The
definitions are frozen, and the compiled registry and validators never change,
so every set built from the same definitions shares one compiled form. The host
builds a set from a package manifest on each processing step, and a compile
checks every schema and every reference again.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from types import MappingProxyType

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError
from referencing import Registry
from referencing.exceptions import Unresolvable
from referencing.jsonschema import DRAFT202012, Schema

from baqylau_extension_api import schema_documents
from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.models.documents import EncodedDocument, SchemaDefinition, SchemaRef

COMPILED_SETS = 256


class SchemaSet:
    """Keep all schema resolution inside a validated package schema set."""

    def __init__(self, definitions: Sequence[SchemaDefinition]) -> None:
        """Validate schemas and prepare a registry without remote retrieval."""
        compiled = _compiled(tuple(SchemaDefinition.model_validate(definition) for definition in definitions))
        self._definitions = compiled.definitions
        self._validators = compiled.validators

    def validate(self, document: EncodedDocument) -> None:
        """Validate a document without changing its encoded bytes.

        Raises:
            ExtensionContractError: If the schema or content is invalid.

        """
        document = EncodedDocument.model_validate(document)
        self.definition(document.schema_ref)
        instance = schema_documents.decode_json(document.json_text)
        try:
            self._validators[document.schema_ref].validate(instance)
        except (ValidationError, Unresolvable, RecursionError) as error:
            raise ExtensionContractError(str(error)) from error

    def definition(self, reference: SchemaRef) -> SchemaDefinition:
        """Return the exact schema named by a document.

        Returns:
            The registered definition.

        Raises:
            ExtensionContractError: If the schema is not registered.

        """
        try:
            return self._definitions[reference]
        except KeyError as error:
            message = "document schema is not registered"
            raise ExtensionContractError(message) from error


@dataclass(frozen=True)
class _CompiledSchemas:
    definitions: Mapping[SchemaRef, SchemaDefinition]
    validators: Mapping[SchemaRef, Draft202012Validator]


@lru_cache(maxsize=COMPILED_SETS)
def _compiled(definitions: tuple[SchemaDefinition, ...]) -> _CompiledSchemas:
    registry = _registry(definitions)
    _resolve_references(registry, definitions)
    validators = {
        registered.reference: Draft202012Validator(schema_documents.schema_document(registered), registry=registry)
        for registered in definitions
    }
    return _CompiledSchemas(
        MappingProxyType({stored.reference: stored for stored in definitions}),
        MappingProxyType(validators),
    )


def _registry(definitions: tuple[SchemaDefinition, ...]) -> Registry[Schema]:
    if len({schema_documents.schema_uri(definition) for definition in definitions}) != len(definitions):
        message = "schema owner, name, and version must be unique"
        raise ExtensionContractError(message)
    registry: Registry[Schema] = Registry()
    for definition in definitions:
        document = schema_documents.schema_document(definition)
        try:
            Draft202012Validator.check_schema(document)
        except SchemaError as error:
            raise ExtensionContractError(error.message) from error
        _check_references(document, definitions)
        resource = DRAFT202012.create_resource(document)
        registry = registry.with_resource(schema_documents.schema_uri(definition), resource)
    return registry


def _check_references(document: Schema, definitions: Sequence[SchemaDefinition]) -> None:
    allowed = {schema_documents.schema_uri(definition) for definition in definitions}
    for reference in schema_documents.references(document):
        target = reference.partition("#")[0]
        if target and target not in allowed:
            message = f"schema reference is outside the package: {reference}"
            raise ExtensionContractError(message)


def _resolve_references(registry: Registry[Schema], definitions: Sequence[SchemaDefinition]) -> None:
    for definition in definitions:
        resolver = registry.resolver(schema_documents.schema_uri(definition))
        document = schema_documents.schema_document(definition)
        for reference in schema_documents.references(document):
            try:
                Draft202012Validator.check_schema(resolver.lookup(reference).contents)
            except (SchemaError, Unresolvable) as error:
                message = f"schema reference does not resolve to a valid schema: {reference}"
                raise ExtensionContractError(message) from error
