"""The shared application runtime starts through its public abstraction."""

from __future__ import annotations

import os
import socket

import pytest

from api.runtime import ApplicationConfig
from sdk.client import BaqylauClient
from tests.e2e.testkit.process import ApplicationProcess


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
