# Copyright (c) 2026 Zhambyl Yermagambet
"""Contracts for Claude Code screen controls."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from domain.ids import WindowId


class ScreenDriver(Protocol):
    """Read a composer screen and send navigation keys."""

    def read_text(
        self,
        window_id: WindowId,
        extent: str = "screen",
        *,
        ansi: bool = False,
    ) -> str | None:
        """Read terminal text."""
        ...

    def send_key(self, window_id: WindowId, *keys: str) -> bool:
        """Send terminal keys."""
        ...


class TextScreenDriver(ScreenDriver, Protocol):
    """Write text to a screen control."""

    def send_text(self, window_id: WindowId, text: str) -> bool:
        """Send text to the active control."""
        ...


class PasteScreenDriver(ScreenDriver, Protocol):
    """Paste text into a screen control."""

    def paste_text(self, window_id: WindowId, text: str) -> bool:
        """Paste text into the active control."""
        ...


class CommandScreenDriver(ScreenDriver, Protocol):
    """Submit one complete composer command."""

    def insert_text(self, window_id: WindowId, text: str, *, paste: bool = True) -> bool:
        """Put text into the composer without submitting it."""
        ...

    def submit_text(
        self,
        window_id: WindowId,
        text: str,
        *,
        paste: bool = True,
    ) -> bool:
        """Submit text to the composer."""
        ...


class ResizableScreenDriver(ScreenDriver, Protocol):
    """Read and change the terminal viewport height."""

    def lines(self, window_id: WindowId) -> int | None:
        """Return the current viewport height."""
        ...

    def resize_lines(self, window_id: WindowId, cells: int) -> bool:
        """Change the viewport height."""
        ...


class AskScreenDriver(PasteScreenDriver, ResizableScreenDriver, Protocol):
    """Provide the operations that the question dialog uses."""


class RewindScreenDriver(CommandScreenDriver, ResizableScreenDriver, Protocol):
    """Provide the operations that the rewind dialog uses."""
