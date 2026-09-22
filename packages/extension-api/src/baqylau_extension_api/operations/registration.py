# Copyright (c) 2026 Zhambyl Yermagambet
"""Resolve declared operations without importing feature implementations."""

from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.manifest.operations import CommandDefinition, QueryDefinition
from baqylau_extension_api.manifest.package import ExtensionManifest
from baqylau_extension_api.models.environment import ExtensionEnvironment
from baqylau_extension_api.models.operations import OperationBinding


def require_operation_environment(binding: OperationBinding, environment: ExtensionEnvironment) -> None:
    """Reject wrong-owner and stale calls before they reach feature code.

    Raises:
        ExtensionContractError: If the call is outside this worker revision.

    """
    if binding.extension_id != environment.extension_info.extension_id:
        message = "operation belongs to another extension"
        raise ExtensionContractError(message)
    if binding.runtime_revision != environment.runtime_revision:
        message = "operation has a stale runtime revision"
        raise ExtensionContractError(message)


def query_definition(manifest: ExtensionManifest, binding: OperationBinding) -> QueryDefinition:
    """Find one declared read operation for the selected owner and scope.

    Returns:
        The exact schema and scope declaration.

    """
    definition = _find_definition(manifest.contributions.queries, binding.operation_id)
    _require_scope(manifest, definition, binding)
    return definition


def command_definition(manifest: ExtensionManifest, binding: OperationBinding) -> CommandDefinition:
    """Find one declared durable operation for the selected owner and scope.

    Returns:
        The command's schemas, effect class, and reconciliation support.

    """
    definition = _find_definition(manifest.contributions.commands, binding.operation_id)
    _require_scope(manifest, definition, binding)
    return definition


def _find_definition[Definition: QueryDefinition](
    definitions: tuple[Definition, ...], operation_id: str,
) -> Definition:
    for definition in definitions:
        if definition.name == operation_id:
            return definition
    message = "operation is not declared"
    raise ExtensionContractError(message)


def _require_scope(manifest: ExtensionManifest, definition: QueryDefinition, binding: OperationBinding) -> None:
    if binding.extension_id != manifest.extension_id or binding.scope.kind not in definition.scopes:
        message = "operation owner or scope is not declared"
        raise ExtensionContractError(message)
