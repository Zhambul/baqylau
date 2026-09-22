# Copyright (c) 2026 Zhambyl Yermagambet
"""Resolve observer declarations without importing feature code."""

from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.manifest.observers import ObserverSelection
from baqylau_extension_api.manifest.package import ExtensionManifest
from baqylau_extension_api.models.observer_jobs import ObservationJobBinding


def observer_selection(manifest: ExtensionManifest, binding: ObservationJobBinding) -> ObserverSelection:
    """Check the selected owner, capability, and scope before a live callback.

    Returns:
        The declared inputs and external effect policy.

    Raises:
        ExtensionContractError: If the observer is not declared for this binding.

    """
    if binding.extension_id != manifest.extension_id or "observer" not in manifest.capabilities:
        message = "observer binding does not match a declared capability"
        raise ExtensionContractError(message)
    for selection in manifest.contributions.processing:
        if isinstance(selection, ObserverSelection) and binding.scope.kind in selection.scopes:
            return selection
    message = "observer scope is not declared"
    raise ExtensionContractError(message)
