# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep all prototype terminal feature code in one SDK-only backend."""

from dataclasses import dataclass

from baqylau_extension_api.contracts.lifecycle import ExtensionLifecycle
from baqylau_extension_api.contracts.plugin import ExtensionCapabilities, ExtensionFactory, ExtensionPlugin
from baqylau_extension_api.contracts.presentation import ExtensionTerminalPresenter
from baqylau_extension_api.contracts.services import ExtensionHostServices
from baqylau_extension_api.models.lifecycle import (
    ActivationReady,
    ActivationRequest,
    ActivationResult,
    DeactivationRequest,
    DeactivationResult,
    ExtensionInfo,
)
from baqylau_extension_api.terminal.blocks import ListBlock, ListItem, StatusBlock, TableBlock, TableRow, TextBlock
from baqylau_extension_api.terminal.files import DiffBlock, FileTreeBlock, FileTreeItem
from baqylau_extension_api.terminal.layout import SectionBlock
from baqylau_extension_api.terminal.models import TerminalView, TerminalViewRequest
from baqylau_extension_api.terminal.text import TextLine, TextSpan


def line(text: str) -> TextLine:
    """Build one feature-owned display line.

    Returns:
        A plain line with no terminal escape sequences.

    """
    return TextLine(spans=(TextSpan(text=text),))


def files() -> FileTreeBlock:
    """Describe a small visible file tree.

    Returns:
        Directory and file rows in preorder.

    """
    return FileTreeBlock(block_id="files", nodes=(
        FileTreeItem(item_id="src", label="src", node_kind="directory"),
        FileTreeItem(item_id="main", label="main.py", depth=1, tone="warning"),
        FileTreeItem(item_id="readme", label="README.md"),
    ))


def diff() -> DiffBlock:
    """Provide a small unified diff for client rendering.

    Returns:
        A bounded diff with feature-owned paths.

    """
    return DiffBlock(
        block_id="diff", old_path="src/main.py", new_path="src/main.py",
        unified_diff="@@ -1 +1 @@\n-print('old')\n+print('new')\n",
    )


def thread() -> ListBlock:
    """Describe messages read from one recorded thread.

    Returns:
        Message labels, details, and one selection.

    """
    return ListBlock(block_id="thread", selected_id="message-1", entries=(
        ListItem(item_id="message-1", label=line("User"), detail=line("Check the logs.")),
        ListItem(item_id="message-2", label=line("Agent"), detail=line("The check passed.")),
    ))


@dataclass(frozen=True)
class TerminalExample(ExtensionPlugin, ExtensionLifecycle, ExtensionTerminalPresenter):
    """Implement small typed capabilities without any private host imports."""

    identity: ExtensionInfo

    @property
    def extension_info(self) -> ExtensionInfo:
        """The host-selected package identity."""
        return self.identity

    @property
    def capabilities(self) -> ExtensionCapabilities:
        """The fixture's lifecycle and pure terminal layout capabilities."""
        return ExtensionCapabilities(lifecycle=self, terminal=self)

    def activate(self, request: ActivationRequest) -> ActivationResult:
        """Return readiness without creating an external resource.

        Returns:
            The requested runtime revision.

        """
        return ActivationReady(runtime_revision=request.runtime_revision)

    def deactivate(self, request: DeactivationRequest) -> DeactivationResult:
        """Finish without leaving a process or external job.

        Returns:
            A complete stop result.

        """
        return DeactivationResult(runtime_revision=request.runtime_revision)

    def present(self, terminal_request: TerminalViewRequest) -> TerminalView:
        """Compute display data from one immutable request.

        Returns:
            A tree with every supported block type and no executable action.

        """
        return TerminalView(binding=terminal_request.binding, title="Extension prototype", blocks=(
            SectionBlock(block_id="overview", title="Recorded data", children=(
                TextBlock(block_id="intro", content=line("Feature code stays in the extension.")),
                StatusBlock(block_id="status", label="Ready", tone="success"),
                TableBlock(block_id="jobs", columns=("Task", "State"), rows=(
                    TableRow(item_id="job-1", cells=(line("Check"), line("Done"))),
                )),
            )),
            files(), diff(), thread(),
        ))


def build_extension(services: ExtensionHostServices) -> ExtensionPlugin:
    """Build the external layout without importing the host.

    Returns:
        A typed feature implementation.

    """
    return TerminalExample(services.environment.extension_info)


FACTORY: ExtensionFactory = build_extension
