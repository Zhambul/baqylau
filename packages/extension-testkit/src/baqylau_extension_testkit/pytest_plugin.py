# Copyright (c) 2026 Zhambyl Yermagambet
"""Give extension tests a private host started from the installed executable.

Set `BAQYLAU_HOST_EXECUTABLE` to the installed `baqylau-dashboard`. Write the
package under test into `host.roots.packages`, then call `host.start()`.
"""

from __future__ import annotations

from contextlib import ExitStack
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pytest

from baqylau_extension_testkit.client import HostClient
from baqylau_extension_testkit.host_process import HostExecutable, HostProcess
from baqylau_extension_testkit.isolation import PrivateRoots

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


@dataclass
class PrivateHost:
    """Start the host once its packages are in place, and stop it at the end of the test."""

    executable: HostExecutable
    roots: PrivateRoots
    cleanup: ExitStack = field(default_factory=ExitStack)

    def start(self) -> HostClient:
        """Start the host, wait until it is ready, and give its typed client.

        Returns:
            The client of the ready host.

        """
        process = HostProcess.start(self.executable, self.roots)
        self.cleanup.callback(_stopped, process)
        client = HostClient(process.url)
        self.cleanup.callback(client.close)
        client.wait_until_ready(process)
        return client


def pytest_configure(config: pytest.Config) -> None:
    """Register the live-case marker that the runner reads."""
    config.addinivalue_line("markers", "baqylau_live: the case needs a real terminal or harness")


def _stopped(process: HostProcess) -> None:
    exit_code = process.stop()
    if exit_code:
        message = f"the host exited with {exit_code}\n{process.log_tail()}"
        raise AssertionError(message)


@pytest.fixture
def baqylau_host(tmp_path: Path) -> Iterator[PrivateHost]:
    """Give a private host with its own data, packages, workspace, home, and port.

    Yields:
        The host, not started yet.

    """
    host = PrivateHost(HostExecutable.from_environment(), PrivateRoots(tmp_path / "baqylau"))
    with host.cleanup:
        yield host
