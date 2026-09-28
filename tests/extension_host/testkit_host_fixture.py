# Copyright (c) 2026 Zhambyl Yermagambet
"""Start private hosts through the shared test kit from the checkout's host executable."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from baqylau_extension_testkit.client import HostClient
from baqylau_extension_testkit.host_process import HostExecutable, HostProcess
from baqylau_extension_testkit.signoff import signoff

from tests import host_launcher

if TYPE_CHECKING:
    from contextlib import ExitStack

    from baqylau_extension_testkit.isolation import PrivateRoots
    from baqylau_extension_testkit.signoff_models import ReportDocument

EXECUTABLE = HostExecutable(host_launcher.host_executable())


@dataclass(frozen=True)
class RunningHost:
    """Keep one started host and its ready client."""

    process: HostProcess
    client: HostClient


def started(cleanup: ExitStack, roots: PrivateRoots) -> RunningHost:
    """Start a host over the roots, and stop it at the end.

    Returns:
        The running host.

    """
    process = HostProcess.start(EXECUTABLE, roots)
    cleanup.callback(require_clean_stop, process)
    client = HostClient(process.url)
    cleanup.callback(client.close)
    client.wait_until_ready(process)
    return RunningHost(process, client)


def require_clean_stop(process: HostProcess) -> None:
    """Stop the host and require exit code 0."""
    assert process.stop() == 0, process.log_tail()


def signed_off_input(client: HostClient) -> ReportDocument | None:
    """Sign off once the source's input is stored.

    Returns:
        The clean report after raw input, or None before it.

    """
    report = signoff(client)
    return report if report.raw_event_count else None
