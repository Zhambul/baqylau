# Copyright (c) 2026 Zhambyl Yermagambet
"""Preserve source output, progress, and first acceptance through retries and restart."""

from pathlib import Path

from extensions.models.source_reads import source_key
from repository.impl.sqlite.source_reads import SqliteExtensionSourceRepository
from tests.extension_host import (
    lifecycle_fixture as lifecycle,
    observation_fixture as originals,
    observation_upgrade_fixture as upgrades,
    source_read_fixture as fixtures,
)


def test_complete_source_commit(tmp_path: Path) -> None:
    """One successful call saves the exact original and a source checkpoint without a fake session."""
    case = fixtures.installed(tmp_path)
    request = fixtures.proposal(case)
    outcome = case.store.record_source_read(request)
    assert case.original.store.pending_observations(10) == outcome.observations.accepted
    expected = request.proposal.response.observations[0]
    assert originals.original(outcome.observations.accepted[0]).candidate == expected.observation
    assert case.store.find_source_read(case.request.context.binding.runtime_revision, "read-1") == request
    assert case.store.source_checkpoint(source_key(case.request)) == outcome.checkpoint
    assert outcome.checkpoint.revision == 1 and outcome.checkpoint.position == "position-1"
    originals.require_no_fake_session(case.store.database)


def test_exact_retry_does_not_requeue(tmp_path: Path) -> None:
    """An accepted retry does not create work after its original input was consumed."""
    case = fixtures.installed(tmp_path)
    request = fixtures.proposal(case)
    first = case.store.record_source_read(request)
    with case.store.database.write() as connection:
        connection.execute("DELETE FROM pending_raw_events")
    repeated = case.store.record_source_read(request.model_copy(update={
        "observed_at": request.observed_at + 1,
    }))
    assert repeated.repeated and not repeated.observations.accepted
    assert repeated.observations.repeated == first.observations.accepted
    assert repeated.checkpoint == first.checkpoint
    assert not case.original.store.pending_observations(10)
    assert case.store.find_source_read(case.request.context.binding.runtime_revision, "read-1") == request


def test_old_retry_does_not_rewind(tmp_path: Path) -> None:
    """A retry reports its first checkpoint while the stored source retains later progress."""
    case = fixtures.installed(tmp_path)
    first_request = fixtures.proposal(case)
    first = case.store.record_source_read(first_request)
    later = case.store.record_source_read(fixtures.proposal(case, "read-2", "position-2"))
    repeated = case.store.record_source_read(first_request)
    assert repeated.repeated and repeated.checkpoint == first.checkpoint
    assert case.store.source_checkpoint(source_key(case.request)) == later.checkpoint
    assert case.original.store.pending_observations(10) == (
        *first.observations.accepted, *later.observations.accepted,
    )


def test_empty_read_progress(tmp_path: Path) -> None:
    """Empty filtered input can move progress once; an unchanged empty read does not change its revision."""
    case = fixtures.installed(tmp_path)
    initial = case.store.record_source_read(fixtures.proposal(case, position=None, emit=False))
    advanced = case.store.record_source_read(fixtures.proposal(case, "read-2", "position-2", emit=False))
    unchanged = case.store.record_source_read(fixtures.proposal(case, "read-3", "position-2", emit=False))
    assert initial.checkpoint.revision == 0
    assert advanced.checkpoint.revision == 1 and advanced.checkpoint == unchanged.checkpoint
    assert not case.original.store.pending_observations(10)
    assert case.store.find_source_read(case.request.context.binding.runtime_revision, "read-3") is not None


def test_restart_after_owner_removal(tmp_path: Path) -> None:
    """Stored reads use retained documents and do not need an enabled worker."""
    case = fixtures.installed(tmp_path)
    request = fixtures.proposal(case)
    outcome = case.store.record_source_read(request)
    lifecycle.commit(case.original.lifecycle, lifecycle.proposal(
        case.original.lifecycle, operation_id="removed",
    ))
    restarted = SqliteExtensionSourceRepository(upgrades.upgraded(case.store.database))
    assert restarted.source_checkpoint(source_key(case.request)) == outcome.checkpoint
    assert restarted.find_source_read(case.request.context.binding.runtime_revision, "read-1") == request
    assert restarted.find_source_read(case.request.context.binding.runtime_revision, "unknown") is None
