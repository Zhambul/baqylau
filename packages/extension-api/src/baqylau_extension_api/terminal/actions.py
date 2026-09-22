# Copyright (c) 2026 Zhambyl Yermagambet
"""Restrict terminal action references to declared command contracts."""

from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.manifest.operations import CommandDefinition
from baqylau_extension_api.manifest.package import ExtensionManifest
from baqylau_extension_api.schemas import SchemaSet
from baqylau_extension_api.terminal.models import TerminalAction, TerminalView


def validate_presentation_actions(manifest: ExtensionManifest, schemas: SchemaSet, response: TerminalView) -> None:
    """Require registered, schema-valid commands with the correct scope.

    Raises:
        ExtensionContractError: If an action has an undeclared scope.

    """
    for action in response.actions:
        command = _declared_command(manifest, action.command_id)
        if response.binding.snapshot.scope.kind not in command.scopes:
            message = "terminal action command scope is not declared"
            raise ExtensionContractError(message)
        _validate_argument_schema(command, action)
        schemas.validate(action.arguments)


def _declared_command(manifest: ExtensionManifest, command_id: str) -> CommandDefinition:
    for command in manifest.contributions.commands:
        if command.name == command_id:
            return command
    message = "terminal action command is not declared"
    raise ExtensionContractError(message)


def _validate_argument_schema(command: CommandDefinition, action: TerminalAction) -> None:
    if action.arguments.schema_ref != command.arguments:
        message = "terminal action arguments do not match the command schema"
        raise ExtensionContractError(message)
    if command.effect == "write" and action.expected_state_revision is None:
        message = "terminal write actions require an expected state revision"
        raise ExtensionContractError(message)
