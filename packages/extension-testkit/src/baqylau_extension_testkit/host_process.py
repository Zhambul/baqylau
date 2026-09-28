# Copyright (c) 2026 Zhambyl Yermagambet
"""Own one host process that runs from an installed executable with private roots."""

from __future__ import annotations

import os
import signal
import subprocess  # noqa: S404 -- The kit starts the installed host executable by its path.
from dataclasses import dataclass
from itertools import chain
from pathlib import Path
from typing import Self

from baqylau_extension_testkit.isolation import PrivateRoots, check_port, free_port

EXECUTABLE_VARIABLE = "BAQYLAU_HOST_EXECUTABLE"
STOP_SECONDS = 15.0
LOG_TAIL_BYTES = 4000


class HostStartError(RuntimeError):
    """Report that the host executable is missing or did not start."""


@dataclass(frozen=True)
class HostExecutable:
    """Name the installed host executable; the kit never guesses a checkout path."""

    path: Path

    @classmethod
    def from_environment(cls) -> Self:
        """Read the executable path from `BAQYLAU_HOST_EXECUTABLE`.

        Returns:
            The named executable.

        Raises:
            HostStartError: If the variable is not set or names no executable file.

        """
        named = os.environ.get(EXECUTABLE_VARIABLE, "")
        path = Path(named).expanduser()
        if not named or not _executable(path):
            message = f"set {EXECUTABLE_VARIABLE} to the installed baqylau-dashboard executable"
            raise HostStartError(message)
        return cls(path)

    def serve_command(self, roots: PrivateRoots, port: int) -> tuple[str, ...]:
        """Build the foreground serve command with private data, packages, and log.

        Returns:
            The command arguments.

        """
        options = (
            ("--port", str(port)), ("--data-dir", str(roots.data_directory)),
            ("--extension-root", str(roots.packages)), ("--log", str(roots.log)),
        )
        return (str(self.path), "serve", *chain.from_iterable(options))


@dataclass
class HostProcess:
    """Own the child process; typed reads go through `HostClient`, not through this class."""

    child: subprocess.Popen[bytes]
    roots: PrivateRoots
    port: int

    @classmethod
    def start(cls, executable: HostExecutable, roots: PrivateRoots, port: int | None = None) -> Self:
        """Start the host on a free or given port.

        Returns:
            The running process; it can still fail before it is ready.

        """
        roots.create()
        selected = check_port(free_port() if port is None else port)
        child = subprocess.Popen(  # noqa: S603 -- A fixed argument list, no shell.
            executable.serve_command(roots, selected),
            env=roots.environment(), cwd=roots.workspace,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return cls(child, roots, selected)

    def __enter__(self) -> Self:
        """Give the running process.

        Returns:
            The same process.

        """
        return self

    def __exit__(self, *_exception: object) -> None:
        """Stop the process at the end of the block."""
        self.stop()

    @property
    def url(self) -> str:
        """Give the base URL of the host."""
        return f"http://127.0.0.1:{self.port}"

    def running(self) -> bool:
        """Tell if the process has not ended.

        Returns:
            True while the process runs.

        """
        return self.child.poll() is None

    def log_tail(self) -> str:
        """Read the end of the host's own output for a failure message.

        Returns:
            The last part of the log, or an empty text.

        """
        if not self.roots.log.exists():
            return ""
        return self.roots.log.read_bytes()[-LOG_TAIL_BYTES:].decode("utf-8", "replace")

    def stop(self) -> int:
        """Send SIGTERM, wait, and kill the process group if it does not stop.

        Returns:
            The exit code.

        Raises:
            HostStartError: If the host needs a kill to stop.

        """
        if self.running():
            self.child.send_signal(signal.SIGTERM)
            try:
                return self.child.wait(STOP_SECONDS)
            except subprocess.TimeoutExpired:
                os.killpg(self.child.pid, signal.SIGKILL)
                self.child.wait(STOP_SECONDS)
                message = f"the host did not stop after SIGTERM\n{self.log_tail()}"
                raise HostStartError(message) from None
        return int(self.child.returncode)


def _executable(path: Path) -> bool:
    return path.is_file() and os.access(path, os.X_OK)
