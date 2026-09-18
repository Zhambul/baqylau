# Copyright (c) 2026 Zhambyl Yermagambet
"""Provide kitty state queries."""

from typing import Protocol

from pydantic import TypeAdapter, ValidationError

from terminal.impl.kitty.remote_commands import KittyRcPayload, KittyRcResponse, LsRcPayload
from terminal.impl.kitty.remote_constants import KITTEN_QUERY_TIMEOUT_SECONDS
from terminal.impl.kitty.remote_socket import KittySocketOperations
from terminal.impl.kitty.remote_tree import KittyOSWindow


class _KittyQueryClient(Protocol):
    def raw(
        self,
        command_name: str,
        payload: KittyRcPayload,
        *,
        want_response: bool = False,
        timeout: float = ...,
    ) -> KittyRcResponse | bool | None:
        """Run a raw socket command."""

    def capture(self, *args: str, timeout: float = KITTEN_QUERY_TIMEOUT_SECONDS) -> str | None:
        """Run a kitten command and capture its text result."""

    def ls(self) -> list[KittyOSWindow] | None:
        """Return the kitty operating-system window tree."""
        ...


class KittyQueryOperations(KittySocketOperations):
    """Provide kitty state queries."""

    def ls(self: _KittyQueryClient) -> list[KittyOSWindow] | None:
        """Return the kitty operating-system window tree.

        Returns:
            The kitty operating-system window tree.

        """
        response = self.raw("ls", LsRcPayload(), want_response=True)
        output = response.response_text if isinstance(response, KittyRcResponse) and response.ok else None
        if output is None:
            output = self.capture("ls", timeout=KITTEN_QUERY_TIMEOUT_SECONDS)
        if output is None:
            return None
        try:
            return TypeAdapter(list[KittyOSWindow]).validate_json(output)
        except ValidationError:
            return None

    def app_focused(self: _KittyQueryClient, tree: list[KittyOSWindow] | None = None) -> bool:
        """Return true when any kitty OS window is focused.

        This is the gate for a launch's `--keep-focus`: kitty's keep-focus
            restore path raises the OS window whenever no kitty window is
            focused, which on macOS activates kitty over the user's current
            app — the dashboard web-launch focus steal. `tree` reuses an `ls`
            the caller already paid for. False on an `ls` failure: a focus
            probe must degrade toward not stealing.

        Returns:
            True when kitty is the frontmost application.

        """
        try:
            windows = self.ls() if tree is None else tree
            return any(os_window.is_focused for os_window in windows or ())
        except Exception:  # noqa: BLE001 - A focus probe must never raise into a launch.
            return False
