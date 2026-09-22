# Copyright (c) 2026 Zhambyl Yermagambet
"""Read extension sources through real checkpoint transactions and a fixed runtime batch."""

from pathlib import Path

from extensions.models.source_reads import source_key
from tests.extension_host import source_processing_fixture as fixtures


def test_source_watches_precede_first_read(tmp_path: Path) -> None:
    """A checked source plan is watched before the worker reads original input."""
    case = fixtures.installed(tmp_path)
    assert case.run() is None
    assert case.probe.trace.actions == ["describe", "watch", "read"]
    paths = case.probe.plan[0].watch_paths
    assert case.watches.paths == frozenset(Path(path) for path in paths)
    request = case.probe.trace.reads[0]
    assert case.probe.trace.grants == [request.context.binding.call_id]
    assert case.original.store.source_checkpoint(source_key(request)).position == "1"


def test_source_pages_are_bounded_per_pass(tmp_path: Path) -> None:
    """A source with more pages yields after its bound and resumes from accepted progress."""
    case = fixtures.installed(tmp_path)
    bound = case.runtime.policy.batches_per_source
    case.probe.behavior.pages = bound + 1
    assert case.run() == case.clock.now + case.runtime.policy.continuation_seconds
    assert len(case.probe.trace.reads) == bound
    assert case.run(refresh=False) is None
    assert len(case.probe.trace.reads) == case.probe.behavior.pages
    assert len(case.probe.trace.descriptions) == 1


def test_timer_read_does_not_reset_its_plan(tmp_path: Path) -> None:
    """A future source deadline waits, then reads the captured descriptor without a new describe."""
    case = fixtures.installed(tmp_path)
    due = case.clock.now + 10
    case.probe.plan = (case.probe.plan[0].model_copy(update={
        "watch_paths": (), "next_due_at": due,
    }),)
    assert case.run() == due
    assert not case.probe.trace.reads
    case.clock.now = due
    assert case.run(refresh=False) is None
    assert len(case.probe.trace.reads) == 1
    assert len(case.probe.trace.descriptions) == 1


def test_read_reply_replaces_deadline(tmp_path: Path) -> None:
    """A read reply can replace the initial timer, then clear it without an idle scan."""
    case = fixtures.installed(tmp_path)
    case.probe.plan = (case.probe.plan[0].model_copy(update={
        "watch_paths": (),
    }),)
    case.probe.behavior.next_due_at = case.clock.now + 10
    assert case.run() == case.probe.behavior.next_due_at
    case.clock.now += 10
    case.probe.behavior.next_due_at = None
    assert case.run(refresh=False) is None
    calls = len(case.probe.trace.reads)
    assert case.run(refresh=False) is None
    assert len(case.probe.trace.reads) == calls


def test_each_call_retains_complete_evidence(tmp_path: Path) -> None:
    """The coordinator saves exactly the request used under its host call grant."""
    case = fixtures.installed(tmp_path)
    case.run()
    request = case.probe.trace.reads[0]
    journal = case.original.store.find_source_read(
        request.context.binding.runtime_revision, request.context.binding.call_id,
    )
    assert journal is not None and journal.proposal.request == request
    assert journal.proposal.checkpoint.revision == 0
    assert journal.observed_at == case.clock.now
    assert len(case.original.original.store.pending_observations(10)) == 1


def test_deadline_skips_idle_file_source(tmp_path: Path) -> None:
    """Another source's timer must not turn file-only inputs into polled sources."""
    case = fixtures.installed(tmp_path)
    assert case.run() is None
    assert case.run(refresh=False) is None
    assert len(case.probe.trace.reads) == 1
    case.run()
    positions = tuple(request.after_position for request in case.probe.trace.reads)
    assert positions == (None, "1")
