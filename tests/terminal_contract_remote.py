# Copyright (c) 2026 Zhambyl Yermagambet
"""Fake kitty remotes for terminal contract tests."""

from __future__ import annotations

from typing import TYPE_CHECKING, Never

from pydantic import TypeAdapter

from terminal.impl.kitty.remote import KittyOSWindow, KittyRcPayload, KittyRcResponse, KittyRemote

if TYPE_CHECKING:
    from terminal.models.values import WindowId

type JsonValue = bool | int | float | str | list[JsonValue] | dict[str, JsonValue] | None
type RemoteTree = tuple[()] | list[dict[str, JsonValue]] | None
type RemoteArgument = str | bool


class SocketRemote(KittyRemote):
    """Record socket calls without starting a kitten process."""

    def __init__(self) -> None:
        """Create empty request records without opening a socket."""
        super().__init__(listen="unix:/unused", kitten="kitten")
        self.raw_calls: list[tuple[str, KittyRcPayload, bool]] = []
        self.raw_timeouts: list[float | None] = []
        self.capture_timeouts: list[float | None] = []

    def raw(
        self,
        command: str,
        payload: KittyRcPayload,
        *,
        want_response: bool = False,
        timeout: float | None = None,
    ) -> KittyRcResponse:
        """Record the request and return a fixed window tree.

        Returns:
            A successful response with one tab and one window.

        """
        self.raw_timeouts.append(timeout)
        self.raw_calls.append((command, payload, want_response))
        return KittyRcResponse(ok=True, data='[{"tabs": [{"id": 3, "windows": [{"id": 7}]}]}]')

    def capture(self, *arguments: str, timeout: float | None = None) -> Never:
        """Reject process use in a socket-only test.

        Raises:
            AssertionError: For every process capture request.

        """
        self.capture_timeouts.append(timeout)
        msg = f"unexpected kitten process for {self.listen}: {arguments}"
        raise AssertionError(msg)


class FakeRemoteInput:
    """Provide input operations for a fake remote."""

    calls: list[tuple[RemoteArgument, ...]]

    def send_text(self, win: str, text: str, *, bracketed: bool = False) -> bool:
        """Record a text send request.

        Returns:
            True for every request.

        """
        self.calls.append(("send-text", win, text, bracketed))
        return True

    def insert_text(self, win: str, text: str, *, bracketed: bool = False) -> bool:
        """Record a text insert request.

        Returns:
            True for every request.

        """
        self.calls.append(("insert-text", win, text, bracketed))
        return True


class FakeRemoteScreen:
    """Provide screen operations for a fake remote."""

    tree: list[dict[str, JsonValue]] | None
    screen_text: str
    _read_requests: list[tuple[WindowId, str, bool]]

    def ls(self) -> list[KittyOSWindow] | None:
        """Return parsed fixture tree rows.

        Returns:
            Parsed fixture tree rows.

        """
        if self.tree is None:
            return None
        return TypeAdapter(list[KittyOSWindow]).validate_python(self.tree)

    def read_text(
        self,
        win_id: WindowId,
        extent: str = "screen",
        *,
        ansi: bool = False,
    ) -> str:
        """Return configured screen text.

        Returns:
            Configured screen text.

        """
        self._read_requests.append((win_id, extent, ansi))
        return self.screen_text


class FakeRemote(FakeRemoteInput, FakeRemoteScreen, KittyRemote):
    """Record kitty calls and return configured fixture data."""

    def __init__(self, tree: RemoteTree = (), printed: str = "") -> None:
        """Initialize the object."""
        self.calls: list[tuple[RemoteArgument, ...]] = []
        self.raw_calls: list[tuple[str, KittyRcPayload, bool]] = []
        self.tree: list[dict[str, JsonValue]] | None = None if tree is None else list(tree)
        self.printed = printed
        self.screen_text = "screen text"
        self._capture_timeouts: list[float | None] = []
        self._read_requests: list[tuple[WindowId, str, bool]] = []
        self._raw_timeouts: list[float | None] = []

    def run(self, *arguments: str) -> int:
        """Record a run call.

        Returns:
            Zero for every command.

        """
        self.calls.append(arguments)
        return 0

    def capture(self, *arguments: str, timeout: float | None = None) -> str:
        """Record a capture call.

        Returns:
            The configured command output.

        """
        self._capture_timeouts.append(timeout)
        self.calls.append(arguments)
        return self.printed

    def raw(
        self,
        command: str,
        payload: KittyRcPayload,
        *,
        want_response: bool = False,
        timeout: float | None = None,
    ) -> None:
        """Record a raw call."""
        self._raw_timeouts.append(timeout)
        self.raw_calls.append((command, payload, want_response))
