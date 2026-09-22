# Copyright (c) 2026 Zhambyl Yermagambet
"""Reject unsupported worker declarations before any feature import."""

from baqylau_extension_api.contracts.services import ExtensionHostServices
from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.manifest.data import CapabilityName
from baqylau_extension_api.manifest.validation import require_compatible_api, validate_manifest
from baqylau_extension_api.runtime.worker_models import WorkerLoadRequest
from baqylau_extension_api.versions import API_VERSION

SUPPORTED_CAPABILITIES: frozenset[CapabilityName] = frozenset((
    "lifecycle", "sources", "translator", "raw_transformer", "canonical_transformer", "projector",
    "projection_transformer", "queries", "commands", "terminal", "migrations", "observer",
))


def validate_load(request: WorkerLoadRequest, services: ExtensionHostServices) -> None:
    """Check the declared API, identity, and implemented capability set.

    Raises:
        ExtensionContractError: If the worker cannot serve this package.

    """
    manifest = validate_manifest(request.manifest, request.peer_schemas)
    require_compatible_api(manifest)
    identity = request.environment.extension_info
    if request.environment != services.environment or identity.api_version != API_VERSION:
        message = "worker environment does not match the installed SDK"
        raise ExtensionContractError(message)
    if identity.extension_id != manifest.extension_id or identity.package_version != manifest.package_version:
        message = "worker environment does not match the package declaration"
        raise ExtensionContractError(message)
    if not set(manifest.capabilities) <= SUPPORTED_CAPABILITIES:
        message = "this worker does not support all declared capabilities"
        raise ExtensionContractError(message)
