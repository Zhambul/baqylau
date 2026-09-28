# Copyright (c) 2026 Zhambyl Yermagambet
"""Hold an extension's change stream open in one scope, and count its change frames.

An open stream holds its scope on the host, so the scope's sources stay active
while the test runs, as they do while a pane or page watches the scope.
"""

from __future__ import annotations

import socket
import threading
from contextlib import ExitStack, contextmanager, suppress
from dataclasses import dataclass, field
from functools import partial
from typing import TYPE_CHECKING
from urllib.parse import quote

import httpx

from baqylau_extension_testkit.waiting import HostWaitError, wait_until

if TYPE_CHECKING:
    from collections.abc import Iterator

    from baqylau_extension_testkit.client import HostClient

BOUNDARY_EVENTS = frozenset(("event: changes", "event: reset"))
OPEN_SECONDS = 30.0
# A change stream is idle between frames, so it has no read deadline.
STREAM_TIMEOUT = httpx.Timeout(OPEN_SECONDS, read=None)


@dataclass
class ChangeWatch:
    """Count the change and reset frames of one open stream."""

    frames: int = 0
    opened: threading.Event = field(default_factory=threading.Event)
    lock: threading.Lock = field(default_factory=threading.Lock)
    connection: socket.socket | None = None

    def count(self) -> int:
        """Read the number of frames so far.

        Returns:
            The count.

        """
        with self.lock:
            return self.frames

    def wait_for(self, frames: int, seconds: float) -> None:
        """Wait until the stream sent at least this number of frames."""
        wait_until(partial(self._reached, frames), seconds, self._described)

    def wait_open(self) -> None:
        """Wait until the host accepted the stream.

        Raises:
            HostWaitError: If the host did not accept it in time.

        """
        if not self.opened.wait(OPEN_SECONDS):
            message = "the change stream did not open"
            raise HostWaitError(message)

    def add(self) -> None:
        """Count one frame."""
        with self.lock:
            self.frames += 1

    def close(self) -> None:
        """Shut the connection down, which wakes a reader that waits for the next frame."""
        if self.connection is not None:
            with suppress(OSError):
                self.connection.shutdown(socket.SHUT_RDWR)

    def _reached(self, frames: int) -> bool | None:
        return True if self.count() >= frames else None

    def _described(self) -> str:
        return f"{self.count()} change frames"


@contextmanager
def watching(client: HostClient, extension_id: str, scope: str) -> Iterator[ChangeWatch]:
    """Open the change stream of one extension in one scope for the block, on its own connection.

    Yields:
        The watch, after the host accepted the stream.

    """
    transport = httpx.Client(base_url=client.transport.base_url, timeout=STREAM_TIMEOUT)
    watch = ChangeWatch()
    selection = (transport, f"/api/extensions/{quote(extension_id, safe='')}/changes", scope, watch)
    reader = threading.Thread(target=_follow, args=selection, daemon=True)
    with ExitStack() as cleanup:
        # The callbacks run in reverse order: shut the connection, end the reader, close the client.
        cleanup.callback(transport.close)
        cleanup.callback(reader.join, OPEN_SECONDS)
        cleanup.callback(watch.close)
        reader.start()
        watch.wait_open()
        yield watch


def _follow(transport: httpx.Client, path: str, scope: str, watch: ChangeWatch) -> None:
    # A shutdown of the connection from the test thread ends the read with a transport error.
    with suppress(httpx.HTTPError, RuntimeError):
        _read(transport, path, scope, watch)


def _read(transport: httpx.Client, path: str, scope: str, watch: ChangeWatch) -> None:
    with transport.stream("GET", path, params={"scope": scope}) as response:
        watch.connection = response.extensions["network_stream"].get_extra_info("socket")
        watch.opened.set()
        _count(response, watch)


def _count(response: httpx.Response, watch: ChangeWatch) -> None:
    for line in response.iter_lines():
        if line in BOUNDARY_EVENTS:
            watch.add()
