"""A real Kitty application boundary for terminal journey cases."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from api.runtime import ApplicationConfig
from sdk.client import BaqylauClient
from terminal.impl.kitty.plugin import kitty_plugin
from terminal.impl.kitty.remote import resolve_listen_on
from tests.e2e.testkit.journeys import JourneyDriver
from tests.e2e.testkit.policy import WaitPolicy
from tests.e2e.testkit.process import (
    HARNESS_PARENT_ENVIRONMENT_VARIABLES,
    ApplicationProcess,
    assert_clean_diagnostics,
)
from tests.e2e.testkit.status_colors import KittyTabColorReader
from tests.e2e.testkit.references import References
from tests.e2e.testkit.terminals import PaneGeometry, RealTerminalDriver, TerminalFocus

ORIGIN_WINDOW_ID = os.environ.get("KITTY_WINDOW_ID")


@pytest.fixture(autouse=True)
def real_terminal_identity(
    monkeypatch: pytest.MonkeyPatch,
    isolated_application_files: None,
) -> None:
    del isolated_application_files
    if ORIGIN_WINDOW_ID is not None:
        monkeypatch.setenv("KITTY_WINDOW_ID", ORIGIN_WINDOW_ID)


@pytest.fixture(scope="session")
def application_process(
    tmp_path_factory: pytest.TempPathFactory,
    isolated_codex_home: Path,
    isolated_claude_home: Path,
    claude_workspace_trust: None,
) -> Iterator[ApplicationProcess]:
    del claude_workspace_trust
    if resolve_listen_on() is None:
        pytest.skip("no Kitty remote-control socket is available")
    process = ApplicationProcess.start(ApplicationConfig(
        data_directory=Path(tmp_path_factory.mktemp("baqylau-kitty-data")),
        port=0,
        terminal="kitty",
        notify_telegram=False,
        notify_webpush=False,
        environment_removals=HARNESS_PARENT_ENVIRONMENT_VARIABLES,
        base_environment={
            **os.environ,
            "CODEX_HOME": str(isolated_codex_home),
            "CLAUDE_CONFIG_DIR": str(isolated_claude_home),
            "CLAUDE_CODE_MANAGED_SETTINGS_PATH": str(
                isolated_claude_home / "managed-settings.json"
            ),
        },
    ))
    try:
        yield process
    finally:
        exit_code = process.stop()
        assert exit_code == 0, f"application process exited with {exit_code}"


@pytest.fixture(scope="session")
def client(application_process: ApplicationProcess) -> Iterator[BaqylauClient]:
    running = BaqylauClient(application_process.endpoint.url)
    running.application.wait_until_ready()
    start = running.diagnostics.checkpoint()
    try:
        yield running
        end = running.diagnostics.wait_until_drained()
        assert_clean_diagnostics(
            "the real-terminal E2E run has pipeline findings",
            running.diagnostics.report(start, end),
        )
    finally:
        running.close()


@pytest.fixture
def journey_driver(
    client: BaqylauClient,
    workspace: str,
    application_process: ApplicationProcess,
    wait_policy: WaitPolicy,
    isolated_codex_home: Path,
    isolated_claude_home: Path,
) -> Iterator[JourneyDriver]:
    driver = JourneyDriver(
        client,
        kitty_plugin(),
        workspace,
        application_process.endpoint.port,
        wait_policy,
        launch_environment=(
            ("CODEX_HOME", str(isolated_codex_home)),
            ("CLAUDE_CONFIG_DIR", str(isolated_claude_home)),
            (
                "CLAUDE_CODE_MANAGED_SETTINGS_PATH",
                str(isolated_claude_home / "managed-settings.json"),
            ),
        ),
    )
    try:
        yield driver
    finally:
        driver.close()


@pytest.fixture
def terminal_color_reader() -> KittyTabColorReader:
    return KittyTabColorReader()


@pytest.fixture
def real_terminal_driver(
    client: BaqylauClient,
    wait_policy: WaitPolicy,
) -> RealTerminalDriver:
    return RealTerminalDriver(client, kitty_plugin(), wait_policy)


@pytest.fixture
def terminal_pane_geometries() -> References[PaneGeometry]:
    return References("terminal pane geometry")


@pytest.fixture
def terminal_focuses() -> References[TerminalFocus]:
    return References("terminal focus")
