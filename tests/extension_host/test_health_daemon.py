# Copyright (c) 2026 Zhambyl Yermagambet
"""A source that fails every read is disabled by the host through a recorded failure operation (C10)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from extensions.configuration import CALL_SECONDS_ENVIRONMENT, FAILURE_LIMIT_ENVIRONMENT
from extensions.models.extension_health import HealthState
from tests import control_effect_native_payload as native_payload, terminal_pty_waits
from tests.extension_host import (
    lifecycle_http_fixture as lifecycle,
    package_fixture,
    process_fixture,
    source_daemon_fixture as fixture,
)

if TYPE_CHECKING:
    from pathlib import Path

    from sdk.client import BaqylauClient

WORKING_READ = "        return read_file(source_request)\n"
FAILING_READS = (
    '        raise RuntimeError("the fixture source cannot read")\n',
    "        __import__('time').sleep(3600)\n",
    "        __import__('sys').stderr.write('x' * 2_097_152)\n        __import__('sys').stderr.flush()\n",
)
FAILURE_WAIT_SECONDS = 60
# The daemon stays live; a new runtime may still wait for the engine boundary.
STOPPED_PHASES = frozenset(("closing", "closed", "fenced"))
CALL_SECONDS = "10"
TEST_TIMEOUT_SECONDS = 120
HOOK_HEADERS = (("Content-Type", "application/json"), ("X-Baqylau", "1"))
RECENT_LIMIT = 10


def failed_and_disabled(client: BaqylauClient, owner: str) -> bool:
    """Check health, requested state, and the failure operation together.

    Returns:
        True when the host has disabled the failing owner.

    """
    lifecycle = client.extensions.lifecycle
    health = {entry.extension_id: entry.state for entry in lifecycle.health().extensions}
    requested = {intent.extension_id: intent.enabled for intent in lifecycle.state().requested}
    kinds = {operation.kind for operation in lifecycle.recent_operations(RECENT_LIMIT).operations}
    failed = health.get(owner) == HealthState.FAILED
    return failed and requested.get(owner) is False and "failure" in kinds


def failing_package(directory: Path, wheels: Path, failing_read: str) -> fixture.SourceDaemon:
    """Install the real source package, then change its read to fail.

    Returns:
        The installed source case.

    """
    case = fixture.installed(directory, wheels)
    backend = case.package / "source_backend.py"
    source = backend.read_text(encoding="utf-8")
    assert WORKING_READ in source
    backend.write_text(source.replace(WORKING_READ, failing_read), encoding="utf-8")
    return case


def enable(client: BaqylauClient, owner: str) -> None:
    """Enable the package and wait until the operation ends; its failure disable may follow at once."""
    request = lifecycle.lifecycle_request(client, owner, "enable", "health-enable")
    operation_id = client.extensions.lifecycle.change(owner, request).operation.operation_id
    terminal_pty_waits.wait_until(
        lambda: client.extensions.lifecycle.operation(operation_id).status != "preparing", FAILURE_WAIT_SECONDS,
    )


def post_core_hook(client: BaqylauClient) -> None:
    """Send one native session start hook, which is core input with no extension."""
    hook = client.transport.client.post(
        "/api/harnesses/claude_code/hooks", content=native_payload.hook_payload("SessionStart", "c10-start"),
        headers=HOOK_HEADERS,
    )
    assert hook.is_success


@pytest.mark.timeout(TEST_TIMEOUT_SECONDS)
@pytest.mark.parametrize("failing_read", FAILING_READS, ids=["raises", "hangs", "floods"])
def test_failing_source_is_disabled(
    tmp_path: Path, runtime_wheels: Path, monkeypatch: pytest.MonkeyPatch, failing_read: str,
) -> None:
    """A source that raises, hangs past the call time, or floods its output is failed and disabled (C10).

    Core hook input still makes progress while the source fails, the daemon
    keeps running, and the host limits come from its environment.
    """
    # Short enough for a quick hang case; long enough for worker startup on a busy machine.
    monkeypatch.setenv(CALL_SECONDS_ENVIRONMENT, CALL_SECONDS)
    monkeypatch.setenv(FAILURE_LIMIT_ENVIRONMENT, "2")
    case = failing_package(tmp_path, runtime_wheels, failing_read)
    owner = package_fixture.read_manifest(case.package).extension_id
    with process_fixture.running_catalog(tmp_path) as client:
        enable(client, owner)
        before = fixture.core_progress(case)
        post_core_hook(client)
        terminal_pty_waits.wait_until(lambda: fixture.core_progress(case) > before, FAILURE_WAIT_SECONDS)
        terminal_pty_waits.wait_until(lambda: failed_and_disabled(client, owner), FAILURE_WAIT_SECONDS)
        assert client.extensions.lifecycle.state().phase not in STOPPED_PHASES
