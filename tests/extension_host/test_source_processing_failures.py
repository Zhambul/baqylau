# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep original progress and valid watch plans after rejected source calls."""

from pathlib import Path

import pytest

from extensions.models.source_reads import source_key
from tests.extension_host import source_processing_fixture as fixtures


@pytest.mark.parametrize("failure", ["fail_read", "wrong_binding"])
def test_bad_read_keeps_original_progress(tmp_path: Path, failure: str) -> None:
    """Exceptions and mismatched replies add no input or checkpoint and recover at retry."""
    case = fixtures.installed(tmp_path)
    setattr(case.probe.behavior, failure, True)
    due = case.run()
    request = case.probe.trace.reads[0]
    assert due == case.clock.now + case.runtime.policy.retry_seconds
    assert case.original.store.source_checkpoint(source_key(request)).revision == 0
    assert not case.original.original.store.pending_observations(10)
    setattr(case.probe.behavior, failure, False)
    assert case.run(refresh=False) == due
    case.clock.now += case.runtime.policy.retry_seconds
    assert case.run(refresh=False) is None


def test_failed_describe_retains_watch_plan(tmp_path: Path) -> None:
    """A failed refresh retains known paths but does not read the stale selection."""
    case = fixtures.installed(tmp_path)
    case.run()
    previous = case.watches.paths
    case.probe.behavior.fail_describe = True
    due = case.run()
    assert due == case.clock.now + case.runtime.policy.retry_seconds
    assert case.watches.paths == previous
    assert len(case.probe.trace.reads) == 1
    case.probe.behavior.fail_describe = False
    case.clock.now += case.runtime.policy.retry_seconds
    assert case.run(refresh=False) is None
    positions = tuple(request.after_position for request in case.probe.trace.reads)
    assert positions == (None, "1")


def test_removed_source_is_released(tmp_path: Path) -> None:
    """An empty replacement plan releases its old source and removes its watches."""
    case = fixtures.installed(tmp_path)
    case.run()
    identity = case.probe.plan[0].source_identity
    case.probe.plan = ()
    assert case.run() is None
    assert not case.watches.paths
    released = case.probe.trace.releases[0]
    assert released.source_identity == identity
    assert len(case.probe.trace.reads) == 1


def test_pending_release_waits_for_retry(tmp_path: Path) -> None:
    """Pending cleanup removes watches but retains the source ID until acknowledgment."""
    case = fixtures.installed(tmp_path)
    case.run()
    case.probe.plan = ()
    case.probe.behavior.pending_release = True
    due = case.run()
    assert due == case.clock.now + case.runtime.policy.retry_seconds
    assert not case.watches.paths
    assert case.run(refresh=False) == due
    assert len(case.probe.trace.releases) == 1
    case.probe.behavior.pending_release = False
    case.clock.now += case.runtime.policy.retry_seconds
    assert case.run(refresh=False) is None


def test_complete_release_is_not_repeated(tmp_path: Path) -> None:
    """Later source notices do not repeat an acknowledged removal."""
    case = fixtures.installed(tmp_path)
    case.run()
    case.probe.plan = ()
    case.run()
    case.run()
    assert len(case.probe.trace.releases) == 1
    assert all(not plan.release_pending for plan in case.runtime.plans.scopes.values())
