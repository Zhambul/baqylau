# Copyright (c) 2026 Zhambyl Yermagambet
"""Check full settings documents without inspecting feature JSON in host services."""

from baqylau_extension_api.manifest.settings import SettingsDefinition
from baqylau_extension_api.models.scopes import ExtensionScope
from baqylau_extension_api.schemas import SchemaSet

from extensions.lifecycle_control_contract import LifecycleRequestError
from extensions.models.settings import SettingsOverrides, capture_settings
from extensions.settings_selection import SettingsTarget


def settings_definition(target: SettingsTarget, scope: ExtensionScope) -> SettingsDefinition:
    """Check the declared scope before reading or changing an override.

    Returns:
        The package's data-only settings declaration.

    Raises:
        LifecycleRequestError: If the package does not permit this scope.

    """
    definition = target.package.manifest.settings
    if definition is None or scope.kind not in definition.scopes:
        message = "the selected package does not declare this settings scope"
        raise LifecycleRequestError(message)
    return definition


def validate_overrides(target: SettingsTarget, overrides: SettingsOverrides) -> None:
    """Check complete raw and captured choices against the selected declaration.

    Raises:
        LifecycleRequestError: If an installation override is not declared.

    """
    if overrides.installation is not None:
        definition = target.package.manifest.settings
        if definition is None or "installation" not in definition.scopes:
            message = "the selected package does not declare installation overrides"
            raise LifecycleRequestError(message)
    manifest = target.package.manifest
    capture_settings(manifest, overrides).validate_declaration(manifest, SchemaSet(manifest.schemas))
