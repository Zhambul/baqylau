# Copyright (c) 2026 Zhambyl Yermagambet
"""Supply an explicit in-memory host catalog for protocol tests only."""

from dataclasses import dataclass

from baqylau_extension_api.models.scopes import ExtensionScope
from baqylau_extension_api.runtime.service_provider import ServiceProvider, ServiceProviderLookup


@dataclass
class FixtureProviders(ServiceProviderLookup):
    """Replace immutable peer snapshots without a feature import in the host."""

    entries: tuple[ServiceProvider, ...] = ()

    def get_service_provider(self, owner: str, scope: ExtensionScope) -> ServiceProvider | None:
        """Use installation-scoped fixture settings for each selected package.

        Returns:
            The exact fixture snapshot or an absent package.

        """
        if scope.kind == "installation":
            return next((entry for entry in self.entries if entry.manifest.extension_id == owner), None)
        return None

    def replace(self, provider: ServiceProvider) -> None:
        """Change only one package's host-owned test snapshot."""
        remaining = tuple(
            entry for entry in self.entries if entry.manifest.extension_id != provider.manifest.extension_id
        )
        self.entries = (*remaining, provider)
