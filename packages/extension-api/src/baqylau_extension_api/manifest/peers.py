# Copyright (c) 2026 Zhambyl Yermagambet
"""Check peer compatibility and exclusive contributions without live calls."""

from collections.abc import Iterator
from itertools import combinations

from packaging.specifiers import SpecifierSet

from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.manifest.metadata import PackageDependency
from baqylau_extension_api.manifest.package import ExtensionManifest


def predecessors(manifest: ExtensionManifest, proposed: tuple[ExtensionManifest, ...]) -> tuple[str, ...]:
    """Select compatible dependencies and explicit active order constraints.

    Returns:
        Package IDs that must occur before this package.

    """
    required = set(_compatible_dependencies(manifest, proposed))
    for peer in proposed:
        if peer.extension_id in manifest.load_order.after or manifest.extension_id in peer.load_order.before:
            required.add(peer.extension_id)
    return tuple(sorted(required))


def validate_required_services(manifest: ExtensionManifest, proposed: tuple[ExtensionManifest, ...]) -> None:
    """Reject a required service that is absent or incompatible.

    Raises:
        ExtensionContractError: If a required service cannot be resolved.

    """
    for requirement in manifest.contributions.consumes:
        available = any(
            service.name == requirement.name and SpecifierSet(requirement.version_range).contains(service.version)
            for peer in proposed if peer.extension_id == requirement.owner
            for service in peer.contributions.services
        )
        if requirement.required and not available:
            message = "a required public extension service is absent or incompatible"
            raise ExtensionContractError(message)


def validate_exclusive_views(proposed: tuple[ExtensionManifest, ...]) -> None:
    """Reject overlapping replacements before the runtime switch.

    Raises:
        ExtensionContractError: If two views replace the same scoped target.

    """
    exclusive = tuple(
        view for manifest in proposed for view in manifest.contributions.web
        if view.mode == "replace"
    )
    for first, second in combinations(exclusive, 2):
        same_target = (first.slot, first.target) == (second.slot, second.target)
        if same_target and set(first.scopes) & set(second.scopes):
            message = "exclusive extension views conflict for the same scoped target"
            raise ExtensionContractError(message)


def _compatible_dependencies(manifest: ExtensionManifest, proposed: tuple[ExtensionManifest, ...]) -> Iterator[str]:
    for dependency in manifest.dependencies:
        matching = tuple(peer for peer in proposed if peer.extension_id == dependency.extension_id)
        if _dependency_available(dependency, matching):
            yield dependency.extension_id


def _dependency_available(dependency: PackageDependency, matching: tuple[ExtensionManifest, ...]) -> bool:
    accepted = SpecifierSet(dependency.version_range)
    compatible = bool(matching) and accepted.contains(matching[0].package_version)
    if dependency.required and not compatible:
        message = "a required extension dependency is absent or incompatible"
        raise ExtensionContractError(message)
    return compatible
