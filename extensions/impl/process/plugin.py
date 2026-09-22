# Copyright (c) 2026 Zhambyl Yermagambet
"""Present a process-backed extension through the same public plugin protocol."""

from dataclasses import dataclass

from baqylau_extension_api.contracts.plugin import ExtensionCapabilities, ExtensionPlugin
from baqylau_extension_api.models.lifecycle import ExtensionInfo


@dataclass(frozen=True)
class ProcessExtensionPlugin(ExtensionPlugin):
    """Retain only checked identity and typed remote capabilities."""

    identity: ExtensionInfo
    selected: ExtensionCapabilities

    @property
    def extension_info(self) -> ExtensionInfo:
        """The worker's verified package identity."""
        return self.identity

    @property
    def capabilities(self) -> ExtensionCapabilities:
        """The supported SDK capability proxies for this worker."""
        return self.selected
