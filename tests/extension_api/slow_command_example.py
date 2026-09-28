# Copyright (c) 2026 Zhambyl Yermagambet
"""Add one slow read command to the ordered transform worker (C11 fixture)."""

import time
from dataclasses import dataclass

from baqylau_extension_api.contracts import operations, plugin
from baqylau_extension_api.contracts.services import ExtensionHostServices
from baqylau_extension_api.models import command_results, commands

from tests.extension_api import ordered_transform_example

# Long enough that input processed during the command proves separate work lanes. Shutdown drains a
# running command, so this stays below the test application's stop deadline.
SLOW_SECONDS = 8


class SlowCommands(operations.ExtensionCommands):
    """Finish each command only after a long wait."""

    def execute(self, command_request: commands.CommandRequest) -> command_results.CommandResult:
        """Wait, then return the arguments.

        Returns:
            Success with the arguments as the result.

        """
        time.sleep(SLOW_SECONDS)
        return command_results.CommandSucceeded(binding=command_request.binding, document=command_request.arguments)

    def cancel(self, cancel_request: commands.CommandCancelRequest) -> commands.CommandCancelResult:
        """Report that the command cannot stop early.

        Returns:
            A not-running result.

        """
        return commands.CommandCancelResult(binding=cancel_request.binding, status="not_running")

    def reconcile(self, reconcile_request: commands.CommandReconcileRequest) -> command_results.CommandResult:
        """Return the read again; it has no external effect.

        Returns:
            Success with the recorded arguments.

        """
        return command_results.CommandSucceeded(
            binding=reconcile_request.command.binding, document=reconcile_request.command.arguments,
        )


@dataclass(frozen=True)
class SlowOrderedExample(ordered_transform_example.OrderedExample):
    """Keep both transforms and add the slow command in the same worker."""

    @property
    def capabilities(self) -> plugin.ExtensionCapabilities:
        """The transforms of the ordered example and the slow command."""
        transforms = super().capabilities
        return plugin.ExtensionCapabilities(
            lifecycle=self, raw_transformer=transforms.raw_transformer,
            canonical_transformer=transforms.canonical_transformer, commands=SlowCommands(),
        )


def build_extension(host: ExtensionHostServices) -> plugin.ExtensionPlugin:
    """Construct the feature only in its SDK worker.

    Returns:
        The plugin.

    """
    return SlowOrderedExample(host)
