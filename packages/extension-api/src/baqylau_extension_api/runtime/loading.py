# Copyright (c) 2026 Zhambyl Yermagambet
"""Import feature code only after the isolated worker checks its declaration."""

import importlib

from baqylau_extension_api.contracts.plugin import ExtensionCapabilities, ExtensionPlugin
from baqylau_extension_api.contracts.services import ExtensionHostServices
from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.manifest.data import CapabilityName
from baqylau_extension_api.models.lifecycle import ExtensionInfo
from baqylau_extension_api.runtime.capability_checks import validate_handlers
from baqylau_extension_api.runtime.preparation import validate_load
from baqylau_extension_api.runtime.worker_models import WorkerLoadRequest


def load_backend(request: WorkerLoadRequest, services: ExtensionHostServices) -> ExtensionPlugin:
    """Build and verify an extension in its worker, never in the daemon.

    Returns:
        The instance whose identity and capabilities match the load request.

    Raises:
        ExtensionContractError: If the entry is absent or its result is invalid.

    """
    checked = WorkerLoadRequest.model_validate(request)
    validate_load(checked, services)
    entry = checked.manifest.backend
    if entry is None:
        message = "a backend-free extension does not need a worker"
        raise ExtensionContractError(message)
    factory: object = getattr(importlib.import_module(entry.module), entry.factory)
    if not callable(factory):
        message = "extension factory is not callable"
        raise ExtensionContractError(message)
    plugin: object = factory(services)
    if not isinstance(plugin, ExtensionPlugin):
        message = "extension factory did not return an ExtensionPlugin"
        raise ExtensionContractError(message)
    _validate_plugin(checked, plugin)
    return plugin


def capability_names(capabilities: ExtensionCapabilities) -> tuple[CapabilityName, ...]:
    """Report concrete handlers in the same order for every worker.

    Returns:
        The present capabilities without empty optional handlers.

    """
    names: list[CapabilityName] = ["lifecycle"]
    if capabilities.sources is not None:
        names.append("sources")
    if capabilities.translator is not None:
        names.append("translator")
    names.extend(_processing_names(capabilities))
    if capabilities.queries is not None:
        names.append("queries")
    if capabilities.commands is not None:
        names.append("commands")
    if capabilities.terminal is not None:
        names.append("terminal")
    if capabilities.migrations is not None:
        names.append("migrations")
    return tuple(names)


def _processing_names(capabilities: ExtensionCapabilities) -> tuple[CapabilityName, ...]:
    names: list[CapabilityName] = []
    if capabilities.raw_transformer is not None:
        names.append("raw_transformer")
    if capabilities.canonical_transformer is not None:
        names.append("canonical_transformer")
    if capabilities.projector is not None:
        names.append("projector")
    if capabilities.projection_transformer is not None:
        names.append("projection_transformer")
    if capabilities.observer is not None:
        names.append("observer")
    return tuple(names)


def _validate_plugin(request: WorkerLoadRequest, plugin: ExtensionPlugin) -> None:
    identity = ExtensionInfo.model_validate(plugin.extension_info)
    if identity != request.environment.extension_info:
        message = "extension instance does not match the installed package identity"
        raise ExtensionContractError(message)
    capabilities: object = plugin.capabilities
    if not isinstance(capabilities, ExtensionCapabilities):
        message = "extension capabilities must use the public capability model"
        raise ExtensionContractError(message)
    validate_handlers(capabilities)
    if set(capability_names(capabilities)) != set(request.manifest.capabilities):
        message = "extension handlers do not match the declared capabilities"
        raise ExtensionContractError(message)
