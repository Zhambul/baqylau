# Copyright (c) 2026 Zhambyl Yermagambet
"""Provide host-verified package identity to an external worker factory."""

from baqylau_extension_api.models.base import Identifier, WireModel
from baqylau_extension_api.models.lifecycle import ExtensionInfo


class ExtensionEnvironment(WireModel):
    """Pin installed artifact identity and the prepared runtime revision."""

    extension_info: ExtensionInfo
    runtime_revision: Identifier
