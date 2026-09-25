# Copyright (c) 2026 Zhambyl Yermagambet
"""Lifecycle changes while input arrives keep each input once and one worker (C02, C04, P08-T03)."""

from __future__ import annotations

from threading import Event
from typing import TYPE_CHECKING, Literal

import pytest

from tests import terminal_pty_waits
from tests.extension_api import operation_samples
from tests.extension_host import (
    lifecycle_http_fixture as lifecycle,
    process_fixture,
    shutdown_process_fixture as processes,
    source_daemon_fixture as source,
)

if TYPE_CHECKING:
    from pathlib import Path

    from sdk.client import BaqylauClient

FIRST, SECOND, THIRD = '"first"\n', '"second"\n', '"third"\n'
DISABLED_WAIT_SECONDS = 2
TEST_TIMEOUT_SECONDS = 180


def require_one_worker(directory: Path) -> None:
    """Wait until exactly one worker runs; a stopped worker can take a moment to exit."""
    terminal_pty_waits.wait_until(lambda: one_worker(directory))


def one_worker(directory: Path) -> bool:
    """Tell if exactly one worker of this test runs.

    Returns:
        True for one worker.

    """
    try:
        processes.worker_processes(directory, 1)
    except AssertionError:
        return False
    return True


def change(client: BaqylauClient, action: Literal["enable", "disable"], request_id: str) -> None:
    """Change the source package with a new request ID and wait for success."""
    request = lifecycle.lifecycle_request(client, operation_samples.OWNER, action, request_id)
    admitted = client.extensions.lifecycle.change(operation_samples.OWNER, request)
    assert lifecycle.wait_operation(client, admitted.operation.operation_id).status == "succeeded"


def disable_and_enable_again(case: source.SourceDaemon, client: BaqylauClient) -> None:
    """Write input while disabled; it waits, and the re-enabled source reads it once."""
    change(client, "disable", "disable")
    case.append(SECOND)
    assert not Event().wait(DISABLED_WAIT_SECONDS)
    assert case.texts() == (FIRST,)
    change(client, "enable", "enable-again")
    source.require_facts(case, (FIRST, SECOND))


@pytest.mark.timeout(TEST_TIMEOUT_SECONDS)
def test_reenable_and_restart_keep_input_once(tmp_path: Path, runtime_wheels: Path) -> None:
    """Input written while disabled is read once after re-enable; a restart reads new input once; one worker runs."""
    case = source.installed(tmp_path, runtime_wheels)
    with process_fixture.running_catalog(tmp_path) as client:
        case.change(client, "enable")
        source.require_facts(case, (FIRST,))
        require_one_worker(tmp_path)
        disable_and_enable_again(case, client)
        require_one_worker(tmp_path)
    case.append(THIRD)
    with process_fixture.running_catalog(tmp_path):
        source.require_facts(case, (FIRST, SECOND, THIRD))
        require_one_worker(tmp_path)
    assert case.texts() == (FIRST, SECOND, THIRD)


@pytest.mark.timeout(TEST_TIMEOUT_SECONDS)
def test_failed_reload_keeps_processing(tmp_path: Path, runtime_wheels: Path) -> None:
    """A reload whose new code cannot load fails; the old worker keeps reading new input."""
    case = source.installed(tmp_path, runtime_wheels)
    with process_fixture.running_catalog(tmp_path) as client:
        case.change(client, "enable")
        source.require_facts(case, (FIRST,))
        broken = case.package / "source_backend.py"
        broken.write_text("raise RuntimeError('the new version cannot load')\n", encoding="utf-8")
        client.extensions.rescan(client.extensions.catalog().revision)
        request = lifecycle.lifecycle_request(client, operation_samples.OWNER, "reload", "broken-reload")
        admitted = client.extensions.lifecycle.change(operation_samples.OWNER, request)
        case.append(SECOND)
        assert lifecycle.wait_operation(client, admitted.operation.operation_id).status == "failed"
        case.append(THIRD)
        source.require_facts(case, (FIRST, SECOND, THIRD))
        require_one_worker(tmp_path)
