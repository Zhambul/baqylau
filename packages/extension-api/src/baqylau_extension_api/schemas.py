# Copyright (c) 2026 Zhambyl Yermagambet
"""Validate extension documents against an immutable local schema set."""

from collections.abc import Sequence

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError
from referencing import Registry
from referencing.exceptions import Unresolvable
from referencing.jsonschema import DRAFT202012, Schema

from baqylau_extension_api import schema_documents
from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.models.documents import EncodedDocument, SchemaDefinition, SchemaRef


class SchemaSet:
    """Keep all schema resolution inside a validated package schema set."""

    def __init__(self, definitions: Sequence[SchemaDefinition]) -> None:
        """Validate schemas and prepare a registry without remote retrieval."""
        self._initialize(tuple(SchemaDefinition.model_validate(definition) for definition in definitions))

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

    def _initialize(self, definitions: Sequence[SchemaDefinition]) -> None:
        self._definitions = {definition.reference: definition for definition in definitions}
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
            self._check_references(document, definitions)
            resource = DRAFT202012.create_resource(document)
            registry = registry.with_resource(schema_documents.schema_uri(definition), resource)
        self._registry = registry
        self._resolve_references(definitions)
        self._validators = {
            registered.reference: Draft202012Validator(
                schema_documents.schema_document(registered), registry=self._registry,
            )
            for registered in definitions
        }

    def _check_references(self, document: Schema, definitions: Sequence[SchemaDefinition]) -> None:
        allowed = {schema_documents.schema_uri(definition) for definition in definitions}
        for reference in schema_documents.references(document):
            target = reference.partition("#")[0]
            if target and target not in allowed:
                message = f"schema reference is outside the package: {reference}"
                raise ExtensionContractError(message)

    def _resolve_references(self, definitions: Sequence[SchemaDefinition]) -> None:
        for definition in definitions:
            resolver = self._registry.resolver(schema_documents.schema_uri(definition))
            document = schema_documents.schema_document(definition)
            for reference in schema_documents.references(document):
                try:
                    Draft202012Validator.check_schema(resolver.lookup(reference).contents)
                except (SchemaError, Unresolvable) as error:
                    message = f"schema reference does not resolve to a valid schema: {reference}"
                    raise ExtensionContractError(message) from error
