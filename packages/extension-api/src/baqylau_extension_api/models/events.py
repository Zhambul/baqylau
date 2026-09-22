# Copyright (c) 2026 Zhambyl Yermagambet
"""Describe immutable inputs and candidate facts at the public boundary."""

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from baqylau_extension_api.models.base import ExtensionId, Identifier, OpaqueId, Revision, WireModel
from baqylau_extension_api.models.documents import ContentReference, EncodedDocument
from baqylau_extension_api.models.scopes import ExtensionScope

MAX_CAUSE_COUNT = 64


class SourceReference(WireModel):
    """Link a derived input to a recorded observation and source position."""

    raw_event_id: OpaqueId
    source_identity: OpaqueId
    source_position: str


class RawInput(WireModel):
    """Provide recorded content to a declared source translator."""

    input_id: OpaqueId
    scope: ExtensionScope
    source_type: Identifier
    source: SourceReference
    content: ContentReference
    origin: Literal["harness", "extension"]
    owner: Identifier


class ExtensionFact(WireModel):
    """Describe a candidate fact before the host allocates its cursor."""

    kind: Literal["extension"] = "extension"
    event_id: OpaqueId
    scope: ExtensionScope
    event_type: Identifier
    document: EncodedDocument
    occurred_at: float | None = None
    causes: Annotated[tuple[OpaqueId, ...], Field(max_length=MAX_CAUSE_COUNT)] = ()

    @model_validator(mode="after")
    def validate_owner(self) -> Self:
        """Require an event type in the schema owner's namespace.

        Returns:
            The validated candidate fact.

        Raises:
            ValueError: If the type owner or cause references are invalid.

        """
        owner = self.document.schema_ref.owner
        if not self.event_type.startswith(f"{owner}.") or self.event_type == f"{owner}.":
            message = "event type must start with the schema owner's namespace"
            raise ValueError(message)
        duplicate_causes = len(set(self.causes)) != len(self.causes)
        if duplicate_causes or self.event_id in self.causes:
            message = "cause references must be unique and must not refer to this event"
            raise ValueError(message)
        return self


class ProcessingContext(WireModel):
    """Supply the recorded inputs needed for repeatable processing."""

    extension_id: ExtensionId
    runtime_revision: Identifier
    history_revision: Identifier
    scope: ExtensionScope
    input_cursor: Revision
    settings_revision: Revision
    settings: EncodedDocument | None = None
    mode: Literal["live", "replay"] = "live"
