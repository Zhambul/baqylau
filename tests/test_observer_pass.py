# Copyright (c) 2026 Zhambyl Yermagambet
"""Run committed observer jobs and settle their output for one enabled package."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from domain.extension_jobs import JobState
from extensions.models.observations import ExtensionObservation
from extensions.observer_execution import CHAIN_LIMIT_CODE, OBSERVER_CHAIN_LIMIT
from extensions.observer_settlement import REJECTED_CODE
from tests import observer_pass_fixture, observer_storage_doubles as storage_doubles
from tests.extension_api import observer_samples

if TYPE_CHECKING:
    from pathlib import Path

    from tests.observer_case import ObserverCase

FACT_COUNT = 1
STALE_MANAGER = "manager-stale"
UNSELECTED_TYPE = "test.sample.unselected"


@pytest.fixture
def case(tmp_path: Path) -> ObserverCase:
    """Commit the observer runtime with its stored trigger.

    Returns:
        The observer case.

    """
    return observer_pass_fixture.a_case(tmp_path)


def test_observer_pass_stores_the_result(case: ObserverCase) -> None:
    """The pass observes one committed fact and stores its checked outcome."""
    assert case.run() == FACT_COUNT

    assert len(case.observer.requests) == 1
    assert case.job().state == JobState.SUCCEEDED
    assert case.cursor() == storage_doubles.COMMIT_CURSOR


def test_output_reenters_as_pending_input(case: ObserverCase) -> None:
    """The settled job's new observation is stored as pending input at a job position."""
    case.run()

    pending = case.pending_inputs()
    assert len(pending) == 1
    stored = pending[0].observation
    assert isinstance(stored, ExtensionObservation)
    job_id = case.job().job_id
    assert stored.source_position == f"{job_id}:0"
    assert stored.candidate.causes == (observer_samples.request().binding.event_id,)


def test_rejected_output_stores_nothing(case: ObserverCase) -> None:
    """A stale manager identity rejects the output, fails the job, and keeps no input."""
    case.run(STALE_MANAGER)

    job = case.job()
    assert job.state == JobState.FAILED
    assert job.diagnostic is not None
    assert REJECTED_CODE in job.diagnostic
    assert case.pending_inputs() == ()


def test_second_run_repeats_no_job_and_no_input(case: ObserverCase) -> None:
    """A second pass over the same cause keeps one job and one pending input."""
    case.run()
    case.run()

    assert len(case.observer.requests) == 1
    assert len(case.pending_inputs()) == 1


def test_unselected_fact_advances_without_a_job(tmp_path: Path) -> None:
    """A fact type that the observer does not select makes no job and no stuck cursor."""
    unselected = observer_pass_fixture.a_case(tmp_path, UNSELECTED_TYPE)

    assert unselected.run() == FACT_COUNT

    assert not unselected.observer.requests
    assert unselected.cursor() == storage_doubles.COMMIT_CURSOR


def test_observer_chain_limit_stops_the_job(case: ObserverCase) -> None:
    """A trigger with too many observer steps behind it fails without an observe call."""
    case.observers.depth = OBSERVER_CHAIN_LIMIT

    case.run()

    job = case.job()
    assert job.state == JobState.FAILED
    assert job.diagnostic is not None
    assert CHAIN_LIMIT_CODE in job.diagnostic
    assert not case.observer.requests
