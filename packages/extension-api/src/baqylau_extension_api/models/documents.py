# Copyright (c) 2026 Zhambyl Yermagambet
"""Carry schema identities and bounded encoded extension content."""

from typing import Annotated

from pydantic import Field

from baqylau_extension_api.models.base import Digest, ExtensionId, Identifier, NonemptyText, Revision, WireModel

MAX_DOCUMENT_CHARACTERS = 1_048_576
DocumentText = Annotated[str, Field(min_length=1, max_length=MAX_DOCUMENT_CHARACTERS)]


class SchemaIdentity(WireModel):
    """Name a schema before its content digest is allocated."""

    owner: ExtensionId
    name: Identifier
    version: Annotated[int, Field(ge=1)]


class SchemaRef(SchemaIdentity):
    """Identify a specific immutable schema owned by one extension."""

    digest: Digest


class EncodedDocument(WireModel):
    """Carry JSON text for validation by the registered schema codec."""

    schema_ref: SchemaRef
    json_text: DocumentText


class SchemaDefinition(WireModel):
    """Bundle a schema with its immutable identity."""

    reference: SchemaRef
    json_text: DocumentText


class ContentReference(WireModel):
    """Refer to stored output without transferring its bytes in each call."""

    content_id: Identifier
    media_type: NonemptyText
    byte_length: Revision
    digest: Digest


class Diagnostic(WireModel):
    """Describe an extension result without an untyped context object."""

    code: Identifier
    message: NonemptyText
    input_id: Identifier | None = None
