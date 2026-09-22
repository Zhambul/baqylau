# Copyright (c) 2026 Zhambyl Yermagambet
"""Capture package state and effective settings without live storage reads."""

from typing import Annotated, Literal

from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.manifest.package import ExtensionManifest
from baqylau_extension_api.manifest.rules import require_unique
from baqylau_extension_api.models.base import Revision, WireModel
from baqylau_extension_api.models.documents import EncodedDocument
from baqylau_extension_api.models.scopes import ExtensionScope
from baqylau_extension_api.operations import documents
from baqylau_extension_api.schemas import SchemaSet
from pydantic import Field


class ScopedRuntimeSettings(WireModel):
    """Supply a complete effective value for one exact scope, not a partial merge."""

    scope: ExtensionScope
    settings: EncodedDocument


class RuntimeSettings(WireModel):
    """Pin the complete settings selection at one owner revision."""

    revision: Revision = 0
    default: EncodedDocument | None = None
    scopes: Annotated[tuple[ScopedRuntimeSettings, ...], Field(max_length=1000)] = ()

    def for_scope(self, scope: ExtensionScope) -> EncodedDocument | None:
        """Read an exact captured value or the captured fallback.

        Returns:
            A fixed effective document, with no storage or feature call.

        """
        for entry in self.scopes:
            if entry.scope == scope:
                return entry.settings
        return self.default

    def validate_declaration(self, manifest: ExtensionManifest, schemas: SchemaSet) -> None:
        """Check every captured document and exact scope against the package declaration.

        Raises:
            ExtensionContractError: If a captured scope is not declared by the package.

        """
        documents.validate_settings(manifest, self.default, schemas)
        require_unique((entry.scope for entry in self.scopes), "registry settings scopes")
        for entry in self.scopes:
            definition = manifest.settings
            if definition is None or entry.scope.kind not in definition.scopes:
                message = "registry settings scope is not declared"
                raise ExtensionContractError(message)
            documents.validate_settings(manifest, entry.settings, schemas)


class RegistryPublication(WireModel):
    """Report a non-blocking compare-and-set result without exposing workers."""

    status: Literal["accepted", "stale", "busy"]
    revision: Revision
