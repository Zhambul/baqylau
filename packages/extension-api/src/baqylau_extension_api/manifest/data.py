# Copyright (c) 2026 Zhambyl Yermagambet
"""Declare schemas and immutable processing selections."""

from typing import Annotated, Literal

from pydantic import Field

from baqylau_extension_api.models.base import Identifier, WireModel
from baqylau_extension_api.models.documents import SchemaRef

type ScopeKind = Literal["session", "workspace", "repository", "installation"]
ScopeKinds = Annotated[
    tuple[ScopeKind, ...], Field(min_length=1, max_length=4),
]
type CapabilityName = Literal[
    "lifecycle", "sources", "translator", "raw_transformer", "canonical_transformer",
    "projector", "projection_transformer", "observer", "queries", "commands", "migrations", "terminal",
]


class DocumentDefinition(WireModel):
    """Register an event type, source type, or record collection."""

    name: Identifier
    schema_ref: SchemaRef
    scopes: ScopeKinds


class ProcessingSelection(WireModel):
    """Select immutable inputs for one pure or post-commit capability."""

    capability: Literal["raw_transformer", "canonical_transformer", "projector", "projection_transformer"]
    scopes: ScopeKinds
    input_types: Annotated[tuple[Identifier, ...], Field(min_length=1, max_length=1000)]
