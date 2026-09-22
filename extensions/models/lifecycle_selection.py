# Copyright (c) 2026 Zhambyl Yermagambet
"""Name exact package and settings selections without live worker objects."""

from typing import Annotated, Self

from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.manifest.package import ExtensionManifest
from baqylau_extension_api.manifest.rules import require_unique
from baqylau_extension_api.models.base import Digest, ExtensionId, Identifier, Revision, WireModel
from baqylau_extension_api.models.lifecycle import ExtensionInfo
from baqylau_extension_api.schemas import SchemaSet
from baqylau_extension_api.versions import API_VERSION
from pydantic import Field, model_validator

from extensions.models.registry import RuntimeSettings
from extensions.models.settings import SettingsOverrides


class RuntimePackageSelection(WireModel):
    """Capture one enabled package and its complete effective settings."""

    extension_info: ExtensionInfo
    settings: RuntimeSettings = RuntimeSettings()

    def validate_manifest(self, manifest: ExtensionManifest, schemas: SchemaSet) -> None:
        """Check package identity and effective settings without a worker or registry."""
        validate_package_identity(self.extension_info, manifest)
        self.settings.validate_declaration(manifest, schemas)


class RuntimeSelection(WireModel):
    """Retain the exact ordered active set for preparation and later recovery."""

    runtime_revision: Identifier
    catalog_revision: Revision
    packages: Annotated[tuple[RuntimePackageSelection, ...], Field(max_length=1000)] = ()

    @model_validator(mode="after")
    def require_unique_packages(self) -> Self:
        """Reject repeated owners before the selection enters storage.

        Returns:
            The checked complete runtime selection.

        """
        require_unique((package.extension_info.extension_id for package in self.packages), "runtime package IDs")
        return self


class ExtensionIntent(WireModel):
    """Retain requested enable state even if preparation fails."""

    extension_id: ExtensionId
    enabled: bool
    package_digest: Digest | None = None

    @model_validator(mode="after")
    def require_enabled_digest(self) -> Self:
        """Require a pinned package for an enable request.

        Returns:
            A complete requested state.

        Raises:
            ValueError: If an enable request has no package identity.

        """
        if self.enabled and self.package_digest is None:
            message = "enabled extension intent requires a package digest"
            raise ValueError(message)
        return self


class OwnerSettings(WireModel):
    """Read one owner's accepted settings independently of enable state."""

    extension_id: ExtensionId
    settings: SettingsOverrides


class SettingsChange(OwnerSettings):
    """Commit a complete checked replacement only after activation succeeds."""

    expected_revision: Revision
    package_digest: Digest

    @model_validator(mode="after")
    def require_next_revision(self) -> Self:
        """Keep revisions increasing by one for an accepted owner change.

        Returns:
            A checked replacement with its exact prior revision.

        Raises:
            ValueError: If the replacement does not advance the selected revision.

        """
        if self.settings.revision != self.expected_revision + 1:
            message = "settings change requires the next owner revision"
            raise ValueError(message)
        return self


def validate_package_identity(identity: ExtensionInfo, manifest: ExtensionManifest) -> None:
    """Check identity before inspecting either complete or unresolved settings.

    Raises:
        ExtensionContractError: If the selected package does not match its declaration.

    """
    if (
        identity.extension_id != manifest.extension_id or identity.package_version != manifest.package_version
        or identity.api_version != API_VERSION
    ):
        message = "runtime package identity does not match its manifest"
        raise ExtensionContractError(message)
