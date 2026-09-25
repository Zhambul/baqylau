# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep uncertain old workers owned and do not repeat acknowledged removal."""

from typing import Final

from baqylau_extension_api.models.lifecycle import DeactivationResult

from extensions.manager_retirement import RetirementOwner, retirement_owner
from extensions.models.cleanup import RetirementIssue
from tests.extension_api import samples, service_samples
from tests.extension_host import registry_fixture, retirement_fixture

REPLACE: Final = "replace"
RETRIED_CALLS = 2


def replaced(owner: RetirementOwner) -> tuple[RetirementIssue, ...]:
    """Retire one owner for a runtime replacement.

    Returns:
        The remaining issues.

    """
    return owner.retire(REPLACE)


def test_acknowledged_runtime_closes_only_once() -> None:
    """Repeated collection cannot deactivate or close a released runtime again."""
    probe = retirement_fixture.RetirementProbe(registry_fixture.peer(service_samples.ALPHA))
    owner = retirement_owner(probe)
    assert not replaced(owner)
    assert not owner.retire("shutdown")
    assert probe.stop_count == 1 and probe.close_count == 1


def test_pending_jobs_keep_worker_owned() -> None:
    """A cancellation request is not proof that the external action stopped."""
    probe = retirement_fixture.RetirementProbe(registry_fixture.peer(service_samples.ALPHA), DeactivationResult(
        runtime_revision=samples.RUNTIME_REVISION, pending_job_ids=("unknown-job",),
    ))
    owner = retirement_owner(probe)
    issue = owner.retire(REPLACE)[0]
    assert issue.reason == "unresolved_jobs" and issue.pending_job_ids == ("unknown-job",)
    assert probe.close_count == 0
    probe.stop_reply = None
    assert not owner.retire(REPLACE)
    assert probe.stop_count == RETRIED_CALLS and probe.close_count == 1


def test_failed_deactivation_can_be_retried() -> None:
    """A failed call retains both its pending identity and resource owner."""
    probe = retirement_fixture.RetirementProbe(registry_fixture.peer(service_samples.ALPHA), fail_stop=True)
    owner = retirement_owner(probe)
    assert owner.retire(REPLACE)[0].reason == "deactivation_failed"
    assert probe.close_count == 0
    probe.fail_stop = False
    assert not owner.retire(REPLACE)
    assert probe.stop_count == RETRIED_CALLS and probe.close_count == 1


def test_lost_worker_closes_with_its_issue() -> None:
    """A worker whose transport is gone cannot answer; its issue stays, and its resources close."""
    probe = retirement_fixture.RetirementProbe(registry_fixture.peer(service_samples.ALPHA), lost_transport=True)
    owner = retirement_owner(probe)
    assert replaced(owner)[0].reason == "deactivation_failed"
    assert owner.closed and probe.close_count == 1
    replaced(owner)
    assert probe.stop_count == 1 and probe.close_count == 1


def test_wrong_revision_cannot_release_worker() -> None:
    """A successful-looking reply for a different runtime is not acknowledgement."""
    probe = retirement_fixture.RetirementProbe(registry_fixture.peer(service_samples.ALPHA), DeactivationResult(
        runtime_revision="wrong-runtime",
    ))
    owner = retirement_owner(probe)
    assert owner.retire(REPLACE)[0].reason == "deactivation_failed"
    assert probe.close_count == 0 and not owner.closed


def test_failed_close_is_not_proof_of_release() -> None:
    """An idempotent second close cannot conceal an earlier incomplete cleanup."""
    probe = retirement_fixture.RetirementProbe(registry_fixture.peer(service_samples.ALPHA), fail_close=True)
    owner = retirement_owner(probe)
    first = owner.retire(REPLACE)
    assert first[0].reason == "close_failed"
    probe.fail_close = False
    assert owner.retire("shutdown") == first
    assert probe.stop_count == 1 and probe.close_count == 1
    assert not owner.closed
