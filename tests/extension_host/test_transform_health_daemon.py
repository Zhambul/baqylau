# Copyright (c) 2026 Zhambyl Yermagambet
"""A hung transform worker is stopped by the call deadline and disabled at the failure limit (C10, P08-T03)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from extensions.configuration import CALL_SECONDS_ENVIRONMENT, FAILURE_LIMIT_ENVIRONMENT
from extensions.models import interpretation_steps as steps
from tests import terminal_pty_waits
from tests.extension_api import ordered_transform_operations as operations
from tests.extension_host import (
    lifecycle_http_fixture as lifecycle,
    ordered_processing_fixture as fixture,
    process_fixture,
    source_daemon_fixture as source,
    test_health_daemon as health_daemon,
)

if TYPE_CHECKING:
    from pathlib import Path

CALL_SECONDS = "3"
# The hung worker's raw call and its canonical call on the same input both fail.
FAILURE_LIMIT = "2"
WAIT_SECONDS = 90
TEST_TIMEOUT_SECONDS = 180


@pytest.mark.timeout(TEST_TIMEOUT_SECONDS)
def test_hung_transform_worker_is_disabled(
    tmp_path: Path, runtime_wheels: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The hung input keeps the other worker's output; the owner is disabled; later input runs without it."""
    monkeypatch.setenv(CALL_SECONDS_ENVIRONMENT, CALL_SECONDS)
    monkeypatch.setenv(FAILURE_LIMIT_ENVIRONMENT, FAILURE_LIMIT)
    case = fixture.installed(tmp_path, runtime_wheels)
    with process_fixture.running_catalog(tmp_path) as client:
        fixture.enable(client)
        case.append('"hang"\n')
        terminal_pty_waits.wait_until(
            lambda: health_daemon.failed_and_disabled(client, operations.FIRST_OWNER), WAIT_SECONDS,
        )
        terminal_pty_waits.wait_until(
            lambda: operations.FIRST_OWNER not in lifecycle.active_owners(client), WAIT_SECONDS,
        )
        case.append('"later"\n')
        source.require_facts(case, ('"hang/raw-second/canonical-second"', '"later/raw-second/canonical-second"'))
        later = source.journals(case)[-1].proposal.steps
        transforms = (steps.RawTransformStep, steps.CanonicalTransformStep)
        owners = {step.request.context.extension_id for step in later if isinstance(step, transforms)}
        assert owners == {operations.SECOND_OWNER}
