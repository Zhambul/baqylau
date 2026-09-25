# Copyright (c) 2026 Zhambyl Yermagambet
"""Declare schemas and immutable processing selections."""

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

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
    """Select immutable inputs for one pure or post-commit capability.

    A canonical transformer that reads the scope's earlier facts sets
    `prior_state`. The host reads and checks up to 1,000 earlier facts for each
    input only when an active transformer asks for them; a transformer that
    does not ask gets an empty snapshot that makes no completeness claim.
    """

    capability: Literal["raw_transformer", "canonical_transformer", "projector", "projection_transformer"]
    scopes: ScopeKinds
    input_types: Annotated[tuple[Identifier, ...], Field(min_length=1, max_length=1000)]
    prior_state: bool = False

    @model_validator(mode="after")
    def require_canonical_prior_state(self) -> Self:
        """Allow prior state only for a canonical transformer.

        Returns:
            The same selection.

        Raises:
            ValueError: If another capability asks for prior state.

        """
        if self.prior_state and self.capability != "canonical_transformer":
            message = "only a canonical transformer can read prior state"
            raise ValueError(message)
        return self
