# Copyright (c) 2026 Zhambyl Yermagambet
"""Present a list whose entries name registered commands, and run those commands (P07-T03 fixture)."""

import hashlib
from dataclasses import dataclass

from baqylau_extension_api.contracts import lifecycle, operations, plugin, presentation, services as host_services
from baqylau_extension_api.models import command_results, commands as command_models, lifecycle as lifecycle_models
from baqylau_extension_api.models.documents import EncodedDocument, SchemaRef
from baqylau_extension_api.terminal.blocks import ListBlock, ListItem
from baqylau_extension_api.terminal.models import TerminalAction, TerminalView, TerminalViewRequest
from baqylau_extension_api.terminal.text import TextLine, TextSpan

TEXT_SCHEMA = '{"type":"string"}'
STATE_REVISION = "state-1"


def argument(owner: str, text: str) -> EncodedDocument:
    """Encode one text argument in the package's own text schema.

    Returns:
        The document.

    """
    digest = hashlib.sha256(TEXT_SCHEMA.encode()).hexdigest()
    reference = SchemaRef(owner=owner, name="text", version=1, digest=digest)
    return EncodedDocument(schema_ref=reference, json_text=f'"{text}"')


def entry(item_id: str, text: str, action_id: str) -> ListItem:
    """List one file with the action that Enter runs on it.

    Returns:
        The list entry.

    """
    label = TextLine(spans=(TextSpan(text=text),))
    return ListItem(item_id=item_id, label=label, action_id=action_id)


class Commands(operations.ExtensionCommands):
    """Answer each command with its own arguments."""

    def execute(self, command_request: command_models.CommandRequest) -> command_results.CommandResult:
        """Finish at once.

        Returns:
            Success with the arguments as the result.

        """
        return command_results.CommandSucceeded(binding=command_request.binding, document=command_request.arguments)

    def cancel(self, cancel_request: command_models.CommandCancelRequest) -> command_models.CommandCancelResult:
        """Report that nothing runs.

        Returns:
            A not-running result.

        """
        return command_models.CommandCancelResult(binding=cancel_request.binding, status="not_running")

    def reconcile(self, reconcile_request: command_models.CommandReconcileRequest) -> command_results.CommandResult:
        """Prove the write from its request.

        Returns:
            Success with the recorded arguments.

        """
        return command_results.CommandSucceeded(
            binding=reconcile_request.command.binding, document=reconcile_request.command.arguments,
        )


@dataclass
class TerminalActionExample(
    plugin.ExtensionPlugin, lifecycle.ExtensionLifecycle, presentation.ExtensionTerminalPresenter,
):
    """Offer a read action and a write action in one list."""

    host: host_services.ExtensionHostServices

    @property
    def extension_info(self) -> lifecycle_models.ExtensionInfo:
        """The host-selected package identity."""
        return self.host.environment.extension_info

    @property
    def capabilities(self) -> plugin.ExtensionCapabilities:
        """The lifecycle, terminal, and command protocols."""
        return plugin.ExtensionCapabilities(lifecycle=self, terminal=self, commands=Commands())

    def activate(self, request: lifecycle_models.ActivationRequest) -> lifecycle_models.ActivationResult:
        """Confirm the selected runtime revision.

        Returns:
            Readiness.

        """
        return lifecycle_models.ActivationReady(runtime_revision=request.runtime_revision)

    def deactivate(self, request: lifecycle_models.DeactivationRequest) -> lifecycle_models.DeactivationResult:
        """Stop with no work left.

        Returns:
            A complete stop.

        """
        return lifecycle_models.DeactivationResult(runtime_revision=request.runtime_revision)

    def present(self, terminal_request: TerminalViewRequest) -> TerminalView:
        """List two files; the focused one is the list selection.

        Returns:
            The list and its two actions.

        """
        owner = self.extension_info.extension_id
        focus = terminal_request.selection
        entries = (entry("readme", "README", "show"), entry("notes", "NOTES", "write"))
        actions = (
            TerminalAction(
                action_id="show", command_id=f"{owner}.show", label="Show", arguments=argument(owner, "readme"),
            ),
            TerminalAction(
                action_id="write", command_id=f"{owner}.write", label="Write", arguments=argument(owner, "notes"),
                expected_state_revision=STATE_REVISION,
            ),
        )
        selected = None if focus is None else focus.item_id
        return TerminalView(binding=terminal_request.binding, title="Files", blocks=(
            ListBlock(block_id="files", entries=entries, selected_id=selected),
        ), actions=actions)


def build_extension(services: host_services.ExtensionHostServices) -> plugin.ExtensionPlugin:
    """Build the fixture from public host services.

    Returns:
        The fixture plugin.

    """
    return TerminalActionExample(services)


FACTORY: plugin.ExtensionFactory = build_extension
