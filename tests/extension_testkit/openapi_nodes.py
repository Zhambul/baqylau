# Copyright (c) 2026 Zhambyl Yermagambet
"""Read the parts of the host's OpenAPI document that name reply fields."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from pydantic import BaseModel, ConfigDict, Field

REFERENCE_PREFIX = "#/components/schemas/"


class _Published(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class SchemaNode(_Published):
    """Keep the parts of one JSON schema that name object properties."""

    reference: str | None = Field(default=None, alias="$ref")
    properties: Mapping[str, SchemaNode] | None = None
    item_schema: SchemaNode | None = Field(default=None, alias="items")
    any_of: tuple[SchemaNode, ...] = Field(default=(), alias="anyOf")


NO_PROPERTIES: Mapping[str, SchemaNode] = MappingProxyType({})


class MediaNode(_Published):
    """Keep the schema of one response media type."""

    media_schema: SchemaNode = Field(alias="schema")


class ResponseNode(_Published):
    """Keep the media types of one response."""

    content: Mapping[str, MediaNode]


class OperationNode(_Published):
    """Keep the responses of one operation."""

    responses: Mapping[str, ResponseNode]


class ComponentsNode(_Published):
    """Keep the named schemas."""

    schemas: Mapping[str, SchemaNode]


class PublishedDocument(_Published):
    """Keep the paths and named schemas of the OpenAPI document."""

    paths: Mapping[str, Mapping[str, OperationNode]]
    components: ComponentsNode

    def resolved(self, schema: SchemaNode) -> SchemaNode:
        """Follow a component reference.

        Returns:
            The referenced schema, or the same schema.

        """
        if schema.reference is None:
            return schema
        return self.components.schemas[schema.reference.removeprefix(REFERENCE_PREFIX)]
