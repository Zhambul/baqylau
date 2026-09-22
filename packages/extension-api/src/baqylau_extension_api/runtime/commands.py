# Copyright (c) 2026 Zhambyl Yermagambet
"""Dispatch jobs and reconciliation through the same public command protocol."""

from dataclasses import dataclass

from pydantic import TypeAdapter

from baqylau_extension_api.contracts.operations import ExtensionCommands
from baqylau_extension_api.models.command_results import CommandResult
from baqylau_extension_api.models.commands import (
    CommandCancelRequest,
    CommandCancelResult,
    CommandReconcileRequest,
    CommandRequest,
)
from baqylau_extension_api.operations import command_results, commands, registration
from baqylau_extension_api.runtime import methods
from baqylau_extension_api.runtime.channel import RpcChannel
from baqylau_extension_api.runtime.codec import ModelHandler
from baqylau_extension_api.runtime.contract import RemoteCaller
from baqylau_extension_api.runtime.worker_models import WorkerLoadRequest
from baqylau_extension_api.schemas import SchemaSet


@dataclass(frozen=True)
class RemoteCommands(ExtensionCommands):
    """Keep command execution, cancellation, and reconciliation distinct."""

    caller: RemoteCaller

    def execute(self, command_request: CommandRequest) -> CommandResult:
        """Dispatch an accepted attempt without a retry inside the proxy.

        Returns:
            The checked job outcome.

        """
        response = self.caller.invoke_typed(
            methods.COMMAND_EXECUTE, command_request, TypeAdapter[CommandResult](CommandResult),
        )
        return command_results.validate_command_response(command_request.binding, response)

    def cancel(self, cancel_request: CommandCancelRequest) -> CommandCancelResult:
        """Request a stop without converting its acknowledgment into a final job state.

        Returns:
            The exact attempt's cancellation acknowledgment.

        """
        response = self.caller.invoke_typed(methods.COMMAND_CANCEL, cancel_request, TypeAdapter(CommandCancelResult))
        return command_results.validate_cancel_response(cancel_request, response)

    def reconcile(self, reconcile_request: CommandReconcileRequest) -> CommandResult:
        """Inspect an uncertain outcome without calling execute again.

        Returns:
            The proven result or an explicit uncertain outcome.

        """
        response = self.caller.invoke_typed(
            methods.COMMAND_RECONCILE, reconcile_request, TypeAdapter[CommandResult](CommandResult),
        )
        return command_results.validate_command_response(reconcile_request.command.binding, response)


@dataclass(frozen=True)
class WorkerCommands(ExtensionCommands):
    """Validate declared jobs and result evidence around each feature call."""

    provider: ExtensionCommands
    load: WorkerLoadRequest
    schemas: SchemaSet

    def execute(self, command_request: CommandRequest) -> CommandResult:
        """Validate before executing one host-accepted command attempt.

        Returns:
            A checked result for later atomic host storage.

        """
        request = commands.validate_command_request(self.load.manifest, self.schemas, command_request)
        registration.require_operation_environment(request.binding, self.load.environment)
        response = command_results.validate_command_response(request.binding, self.provider.execute(request))
        command_results.validate_command_documents(self.load.manifest, self.schemas, response)
        return response

    def cancel(self, cancel_request: CommandCancelRequest) -> CommandCancelResult:
        """Check owner, runtime, and declaration before forwarding cancellation.

        Returns:
            The checked acknowledgment from the selected capability.

        """
        request = commands.validate_cancel_request(self.load.manifest, cancel_request)
        registration.require_operation_environment(request.binding, self.load.environment)
        return command_results.validate_cancel_response(request, self.provider.cancel(request))

    def reconcile(self, reconcile_request: CommandReconcileRequest) -> CommandResult:
        """Check receipt and declaration before inspecting an uncertain result.

        Returns:
            A checked outcome; this method never dispatches execute.

        """
        request = commands.validate_reconcile_request(self.load.manifest, self.schemas, reconcile_request)
        registration.require_operation_environment(request.command.binding, self.load.environment)
        response = command_results.validate_command_response(request.command.binding, self.provider.reconcile(request))
        command_results.validate_command_documents(self.load.manifest, self.schemas, response)
        return response


def register_commands(
    channel: RpcChannel, provider: ExtensionCommands, request: WorkerLoadRequest, schemas: SchemaSet,
) -> None:
    """Keep commands in the live lane with independent cancellation and recovery calls."""
    bound = WorkerCommands(provider, request, schemas)
    channel.register(methods.COMMAND_EXECUTE, ModelHandler(
        CommandRequest, TypeAdapter(CommandResult), bound.execute,
    ), "live")
    channel.register(methods.COMMAND_CANCEL, ModelHandler(
        CommandCancelRequest, TypeAdapter(CommandCancelResult), bound.cancel,
    ), "control")
    channel.register(methods.COMMAND_RECONCILE, ModelHandler(
        CommandReconcileRequest, TypeAdapter(CommandResult), bound.reconcile,
    ), "live")
