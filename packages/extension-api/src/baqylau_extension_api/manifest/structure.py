# Copyright (c) 2026 Zhambyl Yermagambet
"""Validate manifest identity groups and backend capability declarations."""

from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.manifest import rules
from baqylau_extension_api.manifest.package import ExtensionManifest


def validate_identities(manifest: ExtensionManifest) -> None:
    """Check package-level uniqueness and prohibit self dependencies.

    Raises:
        ExtensionContractError: If a package depends on itself.

    """
    rules.require_unique(manifest.capabilities, "capabilities")
    rules.require_unique((asset.path for asset in manifest.assets), "asset paths")
    rules.require_unique((case.case_id for case in manifest.e2e), "E2E case IDs")
    rules.require_unique((peer.extension_id for peer in manifest.dependencies), "dependency IDs")
    _validate_order(manifest)
    for case in manifest.e2e:
        rules.require_unique(case.surfaces, "E2E surfaces")
        rules.require_unique(case.harnesses, "E2E harnesses")
    if any(peer.extension_id == manifest.extension_id for peer in manifest.dependencies):
        message = "an extension cannot depend on itself"
        raise ExtensionContractError(message)


def validate_capabilities(manifest: ExtensionManifest) -> None:
    """Require declarations and registrations to agree before loading code.

    Raises:
        ExtensionContractError: If a backend or required registration is absent.

    """
    declared = frozenset(manifest.capabilities)
    expected = _registered_capabilities(manifest)
    if manifest.backend is not None:
        expected.add("lifecycle")
    if "sources" in declared:
        expected.add("sources")
        expected.add("translator")
    if declared != expected:
        message = "capability declarations must match their registered contributions"
        raise ExtensionContractError(message)
    if declared and manifest.backend is None:
        message = "backend capabilities require a backend entry"
        raise ExtensionContractError(message)
    _validate_optional_capabilities(manifest)


def _registered_capabilities(manifest: ExtensionManifest) -> set[str]:
    contributions = manifest.contributions
    rules.require_unique((selection.capability for selection in contributions.processing), "processing capabilities")
    expected = {selection.capability for selection in contributions.processing}
    registrations = (
        ("translator", contributions.source_types), ("queries", contributions.queries),
        ("commands", contributions.commands), ("terminal", contributions.terminal),
        ("migrations", manifest.migration_paths),
    )
    return {name for name, declarations in registrations if declarations} | expected


def _validate_order(manifest: ExtensionManifest) -> None:
    rules.require_unique(manifest.load_order.before, "before constraints")
    rules.require_unique(manifest.load_order.after, "after constraints")
    constraints = (*manifest.load_order.before, *manifest.load_order.after)
    if manifest.extension_id in constraints:
        message = "an extension cannot order itself"
        raise ExtensionContractError(message)
    if set(manifest.load_order.before) & set(manifest.load_order.after):
        message = "a peer cannot be both before and after this extension"
        raise ExtensionContractError(message)


def _validate_optional_capabilities(manifest: ExtensionManifest) -> None:
    if "sources" in manifest.capabilities and not manifest.contributions.source_types:
        message = "source providers require registered source types"
        raise ExtensionContractError(message)
    if "migrations" in manifest.capabilities and not (manifest.settings or manifest.contributions.collections):
        message = "migration providers require settings or record collections"
        raise ExtensionContractError(message)
