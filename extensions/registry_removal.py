# Copyright (c) 2026 Zhambyl Yermagambet
"""Select required dependents before a manager prepares an atomic removal."""

from baqylau_extension_api.manifest.activation import activation_order
from baqylau_extension_api.manifest.package import ExtensionManifest


def removal_order(
    manifests: tuple[ExtensionManifest, ...], owners: tuple[str, ...],
) -> tuple[str, ...]:
    """Include required dependents and return dependents before their providers.

    Returns:
        A stable removal plan; optional consumers remain available.

    """
    active = activation_order(manifests)
    removed = set(owners)
    _require_selection(active, owners)
    for owner in active:
        if _needs_removed(next(manifest for manifest in manifests if manifest.extension_id == owner), removed):
            removed.add(owner)
    return tuple(selected for selected in reversed(active) if selected in removed)


def _needs_removed(manifest: ExtensionManifest, removed: set[str]) -> bool:
    return any(peer.required and peer.extension_id in removed for peer in manifest.dependencies)


def _require_selection(active: tuple[str, ...], owners: tuple[str, ...]) -> None:
    removed = set(owners)
    invalid = not removed <= set(active)
    repeated = len(removed) != len(owners)
    if invalid or repeated:
        message = "removal requires unique active package IDs"
        raise ValueError(message)
