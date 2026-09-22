# Copyright (c) 2026 Zhambyl Yermagambet
"""Expose ordered transforms as a package with no host runtime imports."""

from dataclasses import dataclass

from baqylau_extension_api.contracts import lifecycle as lifecycle_contract, plugin, services
from baqylau_extension_api.models import lifecycle

from tests.extension_api import ordered_canonical_example, ordered_raw_example


@dataclass(frozen=True)
class OrderedExample(plugin.ExtensionPlugin, lifecycle_contract.ExtensionLifecycle):
    """Keep the two transform capabilities in one private worker."""

    host: services.ExtensionHostServices

    @property
    def extension_info(self) -> lifecycle.ExtensionInfo:
        """The selected package identity."""
        return self.host.environment.extension_info

    @property
    def capabilities(self) -> plugin.ExtensionCapabilities:
        """The exact capabilities declared by this fixture package."""
        return plugin.ExtensionCapabilities(
            lifecycle=self, raw_transformer=ordered_raw_example.OrderedRaw(),
            canonical_transformer=ordered_canonical_example.OrderedCanonical(),
        )

    def activate(self, request: lifecycle.ActivationRequest) -> lifecycle.ActivationResult:
        """Accept the prepared runtime.

        Returns:
            The exact ready revision.

        """
        return lifecycle.ActivationReady(runtime_revision=request.runtime_revision)

    def deactivate(self, request: lifecycle.DeactivationRequest) -> lifecycle.DeactivationResult:
        """Release the fixture with no background jobs.

        Returns:
            The exact stopped revision.

        """
        return lifecycle.DeactivationResult(runtime_revision=request.runtime_revision)


def build_extension(host: services.ExtensionHostServices) -> plugin.ExtensionPlugin:
    """Construct the feature only in its SDK worker.

    Returns:
        A complete explicit plugin implementation.

    """
    return OrderedExample(host)
