# Copyright (c) 2026 Zhambyl Yermagambet
"""Describe new source observations before the host records them."""

from typing import Annotated, Self

from pydantic import Field, model_validator

from baqylau_extension_api.models.base import Identifier, OpaqueId, WireModel
from baqylau_extension_api.models.documents import EncodedDocument
from baqylau_extension_api.models.events import MAX_CAUSE_COUNT
from baqylau_extension_api.models.scopes import ExtensionScope

MAX_OBSERVATIONS = 256


class ObservationCandidate(WireModel):
    """Supply a stable source key and original document, not a committed event."""

    observation_key: Identifier
    source_identity: Identifier
    source_type: Identifier
    scope: ExtensionScope
    document: EncodedDocument
    occurred_at: float | None = None
    causes: Annotated[tuple[OpaqueId, ...], Field(max_length=MAX_CAUSE_COUNT)] = ()

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        """Keep source types owned and cause references unambiguous.

        Returns:
            The checked candidate; stored references still need host validation.

        Raises:
            ValueError: If the source type has a different owner or a cause repeats.

        """
        owner = self.document.schema_ref.owner
        prefix = f"{owner}."
        if not self.source_type.startswith(prefix) or self.source_type == prefix:
            message = "observation source type must use its schema owner's namespace"
            raise ValueError(message)
        if len(set(self.causes)) != len(self.causes):
            message = "observation cause references must be unique"
            raise ValueError(message)
        return self
