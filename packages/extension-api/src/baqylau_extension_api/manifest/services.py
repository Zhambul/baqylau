# Copyright (c) 2026 Zhambyl Yermagambet
"""Validate declared public service operation and dependency references."""

from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.manifest import rules
from baqylau_extension_api.manifest.package import ExtensionManifest


def validate_services(manifest: ExtensionManifest) -> None:
    """Check ownership and local service references without making peer calls."""
    contributions = manifest.contributions
    rules.require_unique((service.name for service in contributions.services), "public service IDs")
    rules.require_owned((service.name for service in contributions.services), manifest.extension_id)
    rules.require_unique((service.name for service in contributions.consumes), "consumed service IDs")
    for service in contributions.services:
        rules.require_unique(service.queries, "public service query IDs")
        rules.require_unique(service.commands, "public service command IDs")
        _validate_exposed(service.queries, tuple(operation.name for operation in contributions.queries))
        _validate_exposed(service.commands, tuple(operation.name for operation in contributions.commands))
    _validate_consumers(manifest)


def _validate_exposed(exposed: tuple[str, ...], registered: tuple[str, ...]) -> None:
    if not set(exposed) <= set(registered):
        message = "public services can expose only registered operations of the correct kind"
        raise ExtensionContractError(message)


def _validate_consumers(manifest: ExtensionManifest) -> None:
    for service in manifest.contributions.consumes:
        rules.require_owned((service.name,), service.owner)
        matching = tuple(peer for peer in manifest.dependencies if peer.extension_id == service.owner)
        if not matching or (service.required and not matching[0].required):
            message = "consumed services require matching package dependencies"
            raise ExtensionContractError(message)
