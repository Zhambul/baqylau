# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep terminal feature layout outside the host client."""

from typing import Protocol, runtime_checkable

from baqylau_extension_api.terminal.models import TerminalView, TerminalViewRequest


@runtime_checkable
class ExtensionTerminalPresenter(Protocol):
    """Build data-only display blocks for a recorded view snapshot."""

    def present(self, terminal_request: TerminalViewRequest) -> TerminalView:
        """Return a layout without writing to the terminal."""
