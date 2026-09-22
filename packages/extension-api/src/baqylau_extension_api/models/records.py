# Copyright (c) 2026 Zhambyl Yermagambet
"""Capture extension records and explicit missing keys at one read boundary."""

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from baqylau_extension_api.models.base import ExtensionId, Identifier, OpaqueId, WireModel
from baqylau_extension_api.models.documents import EncodedDocument, SchemaRef
from baqylau_extension_api.models.scopes import ExtensionScope
from baqylau_extension_api.terminal.text import DisplayText


class RecordKey(WireModel):
    """Keep collection keys inside one owner and data scope."""

    owner: ExtensionId
    collection: Identifier
    scope: ExtensionScope
    key: OpaqueId

    @model_validator(mode="after")
    def validate_collection_owner(self) -> Self:
        """Require a collection in its owner's namespace.

        Returns:
            The checked record key.

        Raises:
            ValueError: If the collection claims another owner.

        """
        prefix = f"{self.owner}."
        if not self.collection.startswith(prefix) or self.collection == prefix:
            message = "record collection must start with its owner's namespace"
            raise ValueError(message)
        return self


class MissingRecord(WireModel):
    """Prove that a selected key has no row at the supplied snapshot."""

    state: Literal["missing"] = "missing"
    key: RecordKey
    revision: Annotated[int, Field(ge=0, le=0)] = 0


class StoredRecord(WireModel):
    """Carry a complete row with its host-allocated commit revision."""

    state: Literal["stored"] = "stored"
    key: RecordKey
    revision: Annotated[int, Field(ge=1)]
    document: EncodedDocument
    summary: DisplayText


class DeletedRecord(WireModel):
    """Keep the last schema and revision after a record is deleted."""

    state: Literal["deleted"] = "deleted"
    key: RecordKey
    revision: Annotated[int, Field(ge=1)]
    schema_ref: SchemaRef


type RecordState = Annotated[MissingRecord | StoredRecord | DeletedRecord, Field(discriminator="state")]
