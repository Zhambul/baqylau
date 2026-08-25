"""Opt-in E2E proof for the installed macOS launch agent."""

from __future__ import annotations

import os

import pytest
from pytest_bdd import scenarios, then, when

from tests.e2e.testkit.restarts import InstalledDaemonRestart

pytestmark = [
    pytest.mark.drift,
    pytest.mark.timeout(60),
    pytest.mark.skipif(
        not os.environ.get("BAQYLAU_E2E_INSTALLED_DAEMON"),
        reason="installed-daemon E2E tests are opt-in",
    ),
]

scenarios("../features/restart.feature")


@pytest.fixture
def installed_daemon_restart() -> InstalledDaemonRestart:
    return InstalledDaemonRestart()


@when("I stop the installed dashboard daemon")
def stop_installed_daemon(installed_daemon_restart: InstalledDaemonRestart) -> None:
    installed_daemon_restart.stop_and_wait_for_replacement()


@then("the installed dashboard health endpoint reports a new process")
def installed_daemon_has_new_process(
    installed_daemon_restart: InstalledDaemonRestart,
) -> None:
    installed_daemon_restart.assert_new_process()


@then("the installed dashboard launch agent is running with automatic startup enabled")
def installed_daemon_has_automatic_launch(
    installed_daemon_restart: InstalledDaemonRestart,
) -> None:
    installed_daemon_restart.assert_automatic_launch_agent()
