# Copyright (c) 2026 Zhambyl Yermagambet
"""Publish typed settings reads and one-scope replacement requests."""

from baqylau_extension_api.models.base import WireModel

from extensions.models.settings_requests import SettingsRequest
from extensions.models.settings_view import SettingsSnapshot


class SettingsChangeRequest(SettingsRequest):
    """Validate a full scope document or explicit reset before host admission."""


class ExtensionSettingsResponse(WireModel):
    """Return accepted settings for one scope and the current write policy."""

    settings: SettingsSnapshot
    read_only: bool
