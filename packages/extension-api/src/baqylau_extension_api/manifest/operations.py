# Copyright (c) 2026 Zhambyl Yermagambet
"""Declare typed operations and versioned peer services."""

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from baqylau_extension_api.manifest.data import ScopeKinds
from baqylau_extension_api.models.base import ExtensionId, Identifier, WireModel
from baqylau_extension_api.models.documents import SchemaRef
from baqylau_extension_api.versions import PackageVersion, VersionRange


class QueryDefinition(WireModel):
    """Bind a read operation to its scope and exact wire schemas."""

    name: Identifier
    scopes: ScopeKinds
    arguments: SchemaRef
    result: SchemaRef


class CommandDefinition(QueryDefinition):
    """Declare external effects before accepting durable work."""

    effect: Literal["read", "write"]
    reconciliation: bool

    @model_validator(mode="after")
    def validate_write(self) -> Self:
        """Require result reconciliation for a command with external effects.

        Returns:
            The checked command declaration.

        Raises:
            ValueError: If an external write cannot be reconciled.

        """
        if self.effect == "write" and not self.reconciliation:
            message = "write commands must support reconciliation"
            raise ValueError(message)
        return self


class PublicService(WireModel):
    """Expose registered operations through a versioned public service."""

    name: Identifier
    version: PackageVersion
    queries: Annotated[tuple[Identifier, ...], Field(max_length=100)] = ()
    commands: Annotated[tuple[Identifier, ...], Field(max_length=100)] = ()


class ServiceRequirement(WireModel):
    """Name a declared peer service with compatible versions."""

    owner: ExtensionId
    name: Identifier
    version_range: VersionRange
    required: bool = True
