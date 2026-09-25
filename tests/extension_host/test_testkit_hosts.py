# Copyright (c) 2026 Zhambyl Yermagambet
"""Two private hosts from the installed executable run together without shared state (P08-T01)."""

from __future__ import annotations

from contextlib import ExitStack
from typing import TYPE_CHECKING

import pytest
from baqylau_extension_testkit.host_process import HostProcess
from baqylau_extension_testkit.isolation import USER_PORT, PrivateRoots, UnsafeFixtureError
from baqylau_extension_testkit.lifecycle import change, rescan
from baqylau_extension_testkit.signoff import signoff
from baqylau_extension_testkit.waiting import wait_until

from tests.extension_api import operation_samples
from tests.extension_host import package_fixture, source_daemon_fixture as source, testkit_host_fixture as hosts

if TYPE_CHECKING:
    from pathlib import Path

WEB_OWNER = "test.web"
SOURCE_OWNER = operation_samples.OWNER
SIGNOFF_SECONDS = 60.0
TEST_TIMEOUT_SECONDS = 180


def web_roots(directory: Path) -> PrivateRoots:
    """Write one web-only package into new private roots.

    Returns:
        The roots.

    """
    roots = PrivateRoots(directory / "web")
    roots.create()
    package_fixture.write_package(roots.packages, WEB_OWNER, web=True)
    return roots


@pytest.mark.timeout(TEST_TIMEOUT_SECONDS)
def test_two_hosts_keep_their_own_state(tmp_path: Path, runtime_wheels: Path) -> None:
    """Each host has its own port, data, and packages; one host's source input is drained and signed off."""
    source_roots = PrivateRoots(tmp_path / "source")
    case = source.installed(source_roots.root, runtime_wheels)
    with ExitStack() as cleanup:
        first = hosts.started(cleanup, source_roots)
        second = hosts.started(cleanup, web_roots(tmp_path))
        assert [entry.extension_id for entry in rescan(second.client).entries] == [WEB_OWNER]
        assert change(first.client, SOURCE_OWNER, "enable", "kit-enable").status == "succeeded"
        case.append('"second"\n')
        assert wait_until(lambda: hosts.signed_off_input(first.client), SIGNOFF_SECONDS, lambda: "no raw input")
        assert signoff(second.client).raw_event_count == 0
        assert first.process.port != second.process.port


def test_user_port_is_refused(tmp_path: Path) -> None:
    """A fixture that names the user's port does not start a process."""
    with pytest.raises(UnsafeFixtureError):
        HostProcess.start(hosts.EXECUTABLE, PrivateRoots(tmp_path), USER_PORT)
