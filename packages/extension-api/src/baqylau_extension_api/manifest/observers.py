# Copyright (c) 2026 Zhambyl Yermagambet
"""Declare post-commit input selection and external effects before loading."""

from typing import Annotated, Literal

from pydantic import Field

from baqylau_extension_api.manifest.data import ProcessingSelection, ScopeKinds
from baqylau_extension_api.models.base import Identifier, WireModel


class ObserverSelection(WireModel):
    """Separate observer write policy from pure processing selections."""

    capability: Literal["observer"] = "observer"
    scopes: ScopeKinds
    input_types: Annotated[tuple[Identifier, ...], Field(min_length=1, max_length=1000)]
    effect: Literal["read", "write"]
    reconciliation: bool = False


type DeclaredProcessingSelection = Annotated[
    ProcessingSelection | ObserverSelection, Field(discriminator="capability"),
]
