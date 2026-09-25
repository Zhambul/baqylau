# Copyright (c) 2026 Zhambyl Yermagambet
"""Cancel, reconcile, and run again the observer jobs that one pass accepted."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from baqylau_extension_api.models.observer_jobs import ObservationJobRequest

from domain.extension_jobs import JobCancelStatus, JobState
from extensions.job_control import JobControl
from extensions.job_requests import JobCancelSubmission, JobKey, JobReconcileSubmission
from extensions.models.registry import RuntimeSettings
from tests import observer_doubles, observer_pass_fixture

if TYPE_CHECKING:
    from pathlib import Path


def test_observer_cancel_stores_a_proven_stop(tmp_path: Path) -> None:
    """Job control stops an accepted observer attempt through the observer capability."""
    case = observer_pass_fixture.a_case(tmp_path)
    case.accept()
    accepted = case.job()

    outcome = JobControl((case.package,), case.stores, None).cancel(
        JobKey(accepted.owner, accepted.scope, accepted.job_id),
        JobCancelSubmission(expected_revision=accepted.revision, reason="stop"),
    )

    assert outcome.status == JobCancelStatus.CANCELED
    assert case.job().state == JobState.CANCELED
    assert not case.observer.requests


def test_observer_reconcile_settles_its_output(tmp_path: Path) -> None:
    """Reconciliation settles the resolved result and its observations without an observe call."""
    case = observer_pass_fixture.a_case(tmp_path)
    case.accept()
    accepted = case.job()

    job = JobControl((case.package,), case.stores, observer_doubles.MANAGER).reconcile(
        JobKey(accepted.owner, accepted.scope, accepted.job_id), JobReconcileSubmission(),
    )

    assert job.state == JobState.SUCCEEDED
    assert not case.observer.requests
    assert len(case.observer.reconciled) == 1
    assert len(case.pending_inputs()) == 1


def test_changed_settings_rebuild_the_request(tmp_path: Path) -> None:
    """A job accepted before a settings change runs with the current settings revision (C23)."""
    case = observer_pass_fixture.a_case(tmp_path)
    case.accept()

    case.execute(replace(case.package, settings=RuntimeSettings(revision=1)))

    assert case.observer.requests[0].settings_revision == 1
    job = case.job()
    assert job.state == JobState.SUCCEEDED
    assert ObservationJobRequest.model_validate_json(job.request).settings_revision == 1
