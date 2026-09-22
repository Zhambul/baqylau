# Copyright (c) 2026 Zhambyl Yermagambet
"""Retain explicit settings overrides without turning defaults into user choices."""

from typing import Annotated, Self

from baqylau_extension_api.manifest.package import ExtensionManifest
from baqylau_extension_api.manifest.rules import require_unique
from baqylau_extension_api.models.base import Revision, WireModel
from baqylau_extension_api.models.documents import EncodedDocument
from pydantic import Field, model_validator

from extensions.models.registry import RuntimeSettings, ScopedRuntimeSettings


class SettingsOverrides(WireModel):
    """An absent installation value uses the selected manifest's current defaults."""

    revision: Revision = 0
    installation: EncodedDocument | None = None
    scopes: Annotated[tuple[ScopedRuntimeSettings, ...], Field(max_length=1000)] = ()

    @model_validator(mode="after")
    def require_distinct_scopes(self) -> Self:
        """Keep installation fallback separate from exact non-installation overrides.

        Returns:
            A complete raw override selection, without default expansion.

        Raises:
            ValueError: If an installation value is repeated as a scoped override.

        """
        require_unique((entry.scope for entry in self.scopes), "settings override scopes")
        if any(entry.scope.kind == "installation" for entry in self.scopes):
            message = "installation settings must use the installation override field"
            raise ValueError(message)
        return self


def capture_settings(manifest: ExtensionManifest, overrides: SettingsOverrides) -> RuntimeSettings:
    """Select full effective values without discarding explicit override choices.

    Returns:
        The selected fallback and exact scope values, without changing stored input.

    """
    fallback = overrides.installation
    if fallback is None and manifest.settings is not None:
        fallback = manifest.settings.defaults
    return RuntimeSettings(revision=overrides.revision, default=fallback, scopes=overrides.scopes)
