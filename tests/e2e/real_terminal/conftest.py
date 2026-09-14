# Copyright (c) 2026 Zhambyl Yermagambet
"""A real Kitty application boundary for terminal journey cases."""

from __future__ import annotations

import contextlib
import os
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from api.runtime import ApplicationConfig
from sdk.client import BaqylauClient
from terminal.impl.kitty import plugin as kitty_plugin_module, remote as kitty_remote
from tests.e2e.testkit import (
    journey_contexts,
    journey_models,
    journeys as journey_testkit,
    policy,
    process as process_testkit,
    terminals as terminal_testkit,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from harness.runtime import HarnessRuntimeConfigs

ORIGIN_WINDOW_ID = os.environ.get("KITTY_WINDOW_ID")


@pytest.fixture(scope="session")
def kitty_data_directory(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Create the data directory of the Kitty test application.

    Returns:
        The data directory that also holds the isolated harness state.

    """
    return Path(tmp_path_factory.mktemp("baqylau-kitty-data"))


@pytest.fixture
def isolated_harness_homes(
    isolated_codex_home: Path,
    isolated_claude_home: Path,
    kitty_data_directory: Path,
) -> journey_contexts.IsolatedHarnessHomes:
    """Return isolated harness configuration directories.

    Returns:
        Isolated harness configuration directories.

    """
    return journey_contexts.IsolatedHarnessHomes(
        isolated_codex_home,
        isolated_claude_home,
        kitty_data_directory / process_testkit.HARNESS_STATE_DIRECTORY,
    )


@pytest.fixture(autouse=True)
def real_terminal_identity(
    monkeypatch: pytest.MonkeyPatch,
    isolated_application_files: None,
) -> None:
    """Process real terminal identity."""
    assert isolated_application_files is None
    if ORIGIN_WINDOW_ID is not None:
        monkeypatch.setenv("KITTY_WINDOW_ID", ORIGIN_WINDOW_ID)


@pytest.fixture(scope="session")
def application_process(
    kitty_data_directory: Path,
    isolated_harness_runtime_configs: HarnessRuntimeConfigs,
    claude_workspace_trust: None,
) -> Iterator[process_testkit.ApplicationProcess]:
    """Start the isolated Kitty test application and stop it after use.

    Yields:
        The running application with the test harness configuration.

    """
    assert claude_workspace_trust is None
    if kitty_remote.resolve_listen_on() is None:
        pytest.skip("no Kitty remote-control socket is available")
    runtime_configs = isolated_harness_runtime_configs
    process = process_testkit.ApplicationProcess.start(
        ApplicationConfig(
            data_directory=kitty_data_directory,
            port=0,
            terminal="kitty",
            notify_telegram=False,
            notify_webpush=False,
            harness_runtime_configs=runtime_configs,
            environment_removals=process_testkit.HARNESS_PARENT_ENVIRONMENT_VARIABLES,
            # A harness that keeps state in the state home writes it here, and
            # not in the file of the person.
            base_environment={
                **os.environ,
                "XDG_STATE_HOME": str(kitty_data_directory / process_testkit.HARNESS_STATE_DIRECTORY),
            },
        ),
    )
    try:
        yield process
    finally:
        exit_code = process.stop()
        assert exit_code == 0, f"application process exited with {exit_code}"


@pytest.fixture(scope="session")
def client(application_process: process_testkit.ApplicationProcess) -> Iterator[BaqylauClient]:
    """Open a client and check diagnostics when terminal tests end.

    Yields:
        The connected client after the application is ready.

    """
    running = BaqylauClient(application_process.endpoint.url)
    running.application.wait_until_ready()
    start = running.diagnostics.checkpoint()
    with contextlib.closing(running):
        yield running
        end = running.diagnostics.wait_until_drained()
        process_testkit.assert_clean_diagnostics(
            "the real-terminal E2E run has pipeline findings",
            running.diagnostics.report(start, end),
        )


@pytest.fixture
def journey_driver(
    client: BaqylauClient,
    workspace: str,
    application_process: process_testkit.ApplicationProcess,
    wait_policy: policy.WaitPolicy,
    isolated_harness_homes: journey_contexts.IsolatedHarnessHomes,
) -> Iterator[journey_testkit.JourneyDriver]:
    """Build a terminal journey driver and close it after the test.

    Yields:
        The driver with the isolated harness directories.

    """
    driver = journey_testkit.JourneyDriver(
        client,
        journey_models.JourneyEnvironment(
            kitty_plugin_module.kitty_plugin(),
            workspace,
            application_process.endpoint.port,
            wait_policy,
            application_process.config.harness_runtime_configs,
            launch_environment=isolated_harness_homes.launch_environment(),
        ),
    )
    try:
        yield driver
    finally:
        driver.close()


@pytest.fixture
def real_terminal_driver(
    client: BaqylauClient,
    wait_policy: policy.WaitPolicy,
) -> terminal_testkit.RealTerminalDriver:
    """Build the real-terminal test driver.

    Returns:
        The driver with the supplied client, Kitty plugin, and wait policy.

    """
    return terminal_testkit.RealTerminalDriver(client, kitty_plugin_module.kitty_plugin(), wait_policy)
