"""The shared application runtime starts through its public abstraction."""

from __future__ import annotations

import os
import socket

import pytest

from api.runtime import ApplicationConfig
from dashboard import cli as dashboard_cli
from domain.ids import HarnessName
from sdk.client import BaqylauClient
from tests.e2e.testkit.process import ApplicationProcess


def test_dashboard_flags_build_one_runtime_config_for_each_harness(tmp_path):
    executable = tmp_path / "provider"
    configuration_directory = tmp_path / "profile"
    arguments = [
        "--harness-executable",
        f"{HarnessName.CLAUDE_CODE}={executable}",
        "--harness-config-dir",
        f"{HarnessName.CLAUDE_CODE}={configuration_directory}",
    ]

    options = dashboard_cli._options(arguments)
    runtime = options.harness_runtime_configs.for_harness(HarnessName.CLAUDE_CODE)

    assert runtime.executable == str(executable)
    assert runtime.configuration_directory == configuration_directory
    assert dashboard_cli._forwarded(arguments) == [
        item
        for flag in options.harness_flags
        for item in (flag.name, flag.value)
    ]


def test_the_application_process_reports_an_automatic_endpoint_and_stops(tmp_path):
    process = ApplicationProcess.start(ApplicationConfig(
        data_directory=tmp_path,
        port=0,
        terminal="pty",
        notify_telegram=False,
        notify_webpush=False,
        base_environment=dict(os.environ),
    ))
    client = BaqylauClient(process.endpoint.url)
    try:
        health = client.application.wait_until_ready()
        assert health.process_id > 0
        assert process.endpoint.port > 0
    finally:
        client.close()
        assert process.stop() == 0


def test_the_application_runtime_reports_a_busy_configured_port(tmp_path):
    with socket.create_server(("127.0.0.1", 0)) as occupied:
        port = int(occupied.getsockname()[1])
        with pytest.raises(AssertionError, match="failed before startup.*exit.*1"):
            ApplicationProcess.start(ApplicationConfig(
                data_directory=tmp_path,
                port=port,
                notify_telegram=False,
                notify_webpush=False,
                base_environment=dict(os.environ),
            ))
