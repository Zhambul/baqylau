# Copyright (c) 2026 Zhambyl Yermagambet
"""The shared application runtime starts through its public abstraction."""

from __future__ import annotations

import os
import socket
from contextlib import ExitStack
from typing import TYPE_CHECKING

import pytest

from api.runtime import ApplicationConfig
from dashboard import cli_forwarding, cli_options
from sdk.client import BaqylauClient
from tests.e2e.testkit.process import ApplicationProcess
from tests.harness_names import CLAUDE_CODE_HARNESS

if TYPE_CHECKING:
    from pathlib import Path


def test_dashboard_flags_build_one_runtime_config(tmp_path: Path) -> None:
    """Verify dashboard flags build one runtime config for each harness."""
    executable = tmp_path / "provider"
    configuration_directory = tmp_path / "profile"
    arguments = [
        "--harness-executable",
        f"{CLAUDE_CODE_HARNESS}={executable}",
        "--harness-config-dir",
        f"{CLAUDE_CODE_HARNESS}={configuration_directory}",
    ]

    options = cli_options.launch_options(arguments)
    runtime = options.harness_runtime_configs.for_harness(CLAUDE_CODE_HARNESS)

    assert runtime.executable == str(executable)
    assert runtime.configuration_directory == configuration_directory
    assert cli_forwarding.forwarded_flags(arguments) == [
        argument for flag in options.harness_flags for argument in (flag.name, flag.setting)
    ]


def test_app_process_reports_auto_endpoint(tmp_path: Path) -> None:
    """Verify the application process reports an automatic endpoint and stops."""
    process = ApplicationProcess.start(
        ApplicationConfig(
            data_directory=tmp_path,
            port=0,
            terminal="pty",
            notify_telegram=False,
            notify_webpush=False,
            base_environment=dict(os.environ),
        ),
    )
    client = BaqylauClient(process.endpoint.url)
    with ExitStack() as cleanup:
        cleanup.callback(process.stop)
        cleanup.callback(client.close)
        health = client.application.wait_until_ready()
        assert health.process_id > 0
        assert process.endpoint.port > 0
    assert process.stop() == 0


def test_app_runtime_reports_busy_configured_port(tmp_path: Path) -> None:
    """Verify the application runtime reports a busy configured port."""
    with socket.create_server(("127.0.0.1", 0)) as occupied:
        port = int(occupied.getsockname()[1])
        with pytest.raises(AssertionError, match=r"failed before startup.*exit.*1"):
            ApplicationProcess.start(
                ApplicationConfig(
                    data_directory=tmp_path,
                    port=port,
                    notify_telegram=False,
                    notify_webpush=False,
                    base_environment=dict(os.environ),
                ),
            )
