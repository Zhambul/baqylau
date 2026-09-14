# Copyright (c) 2026 Zhambyl Yermagambet
"""Provide kitty remote text operations."""

import subprocess  # noqa: S404 -- Send terminal input through the configured kitten executable.
import time
from typing import Protocol

from terminal import paste
from terminal.impl.kitty.remote_commands import GetTextRcPayload, KittyRcPayload, KittyRcResponse
from terminal.impl.kitty.remote_constants import (
    KITTEN_QUERY_TIMEOUT_SECONDS,
    KITTEN_TIMEOUT_SECONDS,
    REMOTE_CONTROL_MARKER,
    SEND_ENTER_DELAY_SECONDS,
    TARGET_OPTION,
)
from terminal.models.values import WindowId


class _KittyTextClient(Protocol):
    kitten: str | None

    def raw(
        self,
        command_name: str,
        payload: KittyRcPayload,
        *,
        want_response: bool = False,
        timeout: float = ...,
    ) -> KittyRcResponse | bool | None:
        """Run a raw socket command."""

    @property
    def listen(self) -> str:
        """The remote-control socket address."""

    def capture(self, *args: str, timeout: float = KITTEN_TIMEOUT_SECONDS) -> str | None:
        """Run a kitten command and capture its text result."""

    def insert_text(self, window_id: WindowId, text: str, *, bracketed: bool = False) -> bool:
        """Insert text without an Enter key.

        Returns:
            True if the insertion command succeeds.

        """


def text_payload(text: str, *, bracketed: bool) -> bytes:
    """Return the bytes that one text input writes.

    The marks of the paste are written HERE, and not by `--bracketed-paste`.
    Kitty closes that option with a SECOND, empty paste, and OpenCode2 answers a
    paste that carries nothing by reading the clipboard of the person into the
    prompt.

    Returns:
        One paste, or the text as it is typed.

    """
    return paste.pasted(text) if bracketed else text.encode("utf-8")


def _send_text_arguments(kitten: str, listen: str, window_id: WindowId) -> list[str]:
    return [kitten, REMOTE_CONTROL_MARKER, TARGET_OPTION, listen, "send-text", "--match", f"id:{window_id}", "--stdin"]


def _insert_text_process(
    kitten: str,
    listen: str,
    window_id: WindowId,
    text: str,
    *,
    bracketed: bool,
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(  # noqa: S603 -- Send text on stdin; the argument list is not a shell command.
        _send_text_arguments(kitten, listen, window_id),
        check=False,
        input=text_payload(text, bracketed=bracketed),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=KITTEN_TIMEOUT_SECONDS,
    )


def _enter_process(kitten: str, listen: str, window_id: WindowId) -> subprocess.CompletedProcess[bytes]:
    time.sleep(SEND_ENTER_DELAY_SECONDS)
    return subprocess.run(  # noqa: S603 -- Send a fixed Enter byte on stdin without a shell.
        _send_text_arguments(kitten, listen, window_id),
        check=False,
        input=b"\r",
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=KITTEN_TIMEOUT_SECONDS,
    )


class KittyTextOperations:
    """Provide kitty remote text operations."""

    def insert_text(self: _KittyTextClient, window_id: WindowId, text: str, *, bracketed: bool = False) -> bool:
        """Insert text without an Enter key.

        Returns:
            True if the insertion command succeeds.

        """
        if self.kitten is None:
            return False
        try:
            result = _insert_text_process(self.kitten, self.listen, window_id, text, bracketed=bracketed)
        except (OSError, subprocess.SubprocessError):
            return False
        return result.returncode == 0

    def send_text(self: _KittyTextClient, window_id: WindowId, text: str, *, bracketed: bool = False) -> bool:
        """Send text and an Enter key.

        Returns:
            True if both text insertion and the Enter key command succeed.

        """
        kitten = self.kitten
        if kitten is None or not self.insert_text(window_id, text, bracketed=bracketed):
            return False
        try:
            completed_process = _enter_process(kitten, self.listen, window_id)
        except (OSError, subprocess.SubprocessError):
            return False
        return completed_process.returncode == 0

    def read_text(
        self: _KittyTextClient,
        window_id: WindowId,
        extent: str = "screen",
        *,
        ansi: bool = False,
    ) -> str | None:
        """Read text from a kitty window.

        Returns:
            Captured text, or None if the command is unavailable or fails.

        """
        response = self.raw(
            "get-text",
            GetTextRcPayload(match=f"id:{window_id}", extent=extent, ansi=ansi),
            want_response=True,
            timeout=KITTEN_QUERY_TIMEOUT_SECONDS,
        )
        if isinstance(response, KittyRcResponse) and response.ok and response.response_text is not None:
            return response.response_text
        arguments = ["get-text", "--match", f"id:{window_id}", "--extent", extent]
        if ansi:
            arguments.append("--ansi")
        return self.capture(*arguments, timeout=KITTEN_QUERY_TIMEOUT_SECONDS)
