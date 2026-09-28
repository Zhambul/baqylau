# Copyright (c) 2026 Zhambyl Yermagambet
"""Run write observers under the host policy with the committed boundary that they saw."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from domain.extension_jobs import JobState
from extensions.control_policy import ExtensionControlPolicy
from extensions.observer_execution import READ_ONLY_CODE
from tests import observer_pass_fixture, observer_storage_doubles as storage_doubles
from tests.extension_api import observer_samples

if TYPE_CHECKING:
    from pathlib import Path

    from tests.observer_case import ObserverCase


@pytest.fixture
def writer(tmp_path: Path) -> ObserverCase:
    """Commit a write observer runtime with its stored trigger.

    Returns:
        The observer case.

    """
    return observer_pass_fixture.a_case(tmp_path, manifest=observer_samples.manifest(effect="write"))


def test_write_observer_waits_in_read_only_mode(writer: ObserverCase) -> None:
    """Read-only mode fails an accepted write observer job before any call."""
    writer.run(policy=ExtensionControlPolicy(read_only=True))

    job = writer.job()
    assert job.state == JobState.FAILED
    assert job.diagnostic is not None
    assert READ_ONLY_CODE in job.diagnostic
    assert not writer.observer.requests


def test_write_observer_gets_the_trigger_revision(writer: ObserverCase) -> None:
    """A write observer runs with the committed boundary that it saw."""
    writer.run()

    expected = f"{storage_doubles.HISTORY_REVISION}:{storage_doubles.COMMIT_CURSOR}"
    assert writer.observer.requests[0].expected_state_revision == expected
