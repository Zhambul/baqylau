# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep settings control separate from feature execution and database access."""

from typing import Protocol

from baqylau_extension_api.models.base import ExtensionId

from extensions.models.lifecycle_state import LifecycleAdmission
from extensions.models.settings_requests import SettingsReadRequest, SettingsRequest
from extensions.models.settings_view import SettingsSnapshot


class ExtensionSettingsControl(Protocol):
    """Read accepted settings and admit one checked scope change."""

    def read_settings(self, extension_id: ExtensionId, request: SettingsReadRequest) -> SettingsSnapshot:
        """Resolve one scope from retained package metadata and accepted overrides."""
        ...

    def change_settings(self, extension_id: ExtensionId, request: SettingsRequest) -> LifecycleAdmission:
        """Prepare a complete runtime before publishing a new accepted override."""
        ...
