# Copyright (c) 2026 Zhambyl Yermagambet
"""Yield between complete interpretations while preserving ordered progress and runtime ownership."""

from functools import partial
from pathlib import Path
from threading import Event
from unittest.mock import Mock

import pytest

from core.work_queue import WorkKind
from engine import mixed_processing
from extensions.registry_snapshot import prepare_snapshot
from tests import storage_reads
from tests.extension_host import mixed_processing_fixture as fixture, registry_memory_fixture


def test_expiry_yields_after_committed_original(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Core reactions run and the runtime is released while later raw input remains."""
    case = fixture.installed(tmp_path, monkeypatch)
    engine = case.engine
    engine.interpreter.translation.accept_interpretation.side_effect = case.expire_after_commit
    before = case.pending()
    case.run()
    assert case.pending() == before[1:]
    case.engine.reactions.drain.assert_called_once()
    case.engine.queue.set_deadline.assert_called_once_with(
        WorkKind.RAW, mixed_processing.CONTINUATION_SECONDS, mixed_processing.CONTINUATION_KEY,
    )
    replacement = prepare_snapshot(100, "replacement", ())
    registry = case.source.runtime.services.registry
    assert registry.publish_snapshot(1, replacement, registry_memory_fixture.MEMORY_COMMIT).status == "accepted"
    engine.interpreter.failures.record.assert_not_called()


def test_continuation_resumes_then_clears_timer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An explicit raw notice finishes the tail without another source read or changed first journal."""
    case = fixture.installed(tmp_path, monkeypatch)
    engine = case.engine
    engine.interpreter.translation.accept_interpretation.side_effect = case.expire_after_commit
    stores = case.source.runtime.stores
    first = case.pending()[0].observation.raw_event_id
    case.run()
    journal = storage_reads.find_interpretation(stores.facts, "default", first)
    case.run()
    case.run()
    assert not case.pending()
    assert storage_reads.find_interpretation(stores.facts, "default", first) == journal
    assert engine.interpreter.translation.accept_interpretation.call_count == fixture.ORIGINAL_COUNT
    case.engine.queue.set_deadline.assert_called_with(WorkKind.RAW, None, mixed_processing.CONTINUATION_KEY)
    case.engine.interpreter.read_sources.assert_not_called()


def test_expired_interval_still_makes_progress(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A slow page read cannot cause endless zero-progress continuations."""
    case = fixture.installed(tmp_path, monkeypatch)
    engine = case.engine
    before = case.pending()
    times = [case.clock.now, case.clock.now + mixed_processing.MIXED_SECONDS]
    clock = Mock(side_effect=[*times, times[-1]])
    monkeypatch.setattr(mixed_processing, "monotonic", clock)
    case.run()
    assert case.pending() == before[1:]
    engine.interpreter.translation.accept_interpretation.assert_called_once()
    case.engine.queue.set_deadline.assert_called_once_with(
        WorkKind.RAW, mixed_processing.CONTINUATION_SECONDS, mixed_processing.CONTINUATION_KEY,
    )
    engine.interpreter.failures.record.assert_not_called()


def test_wall_clock_does_not_end_slice(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Completion timestamps can advance without changing elapsed-time scheduling."""
    case = fixture.installed(tmp_path, monkeypatch)
    engine = case.engine
    engine.interpreter.translation.accept_interpretation.side_effect = case.change_wall_time
    case.run()
    assert not case.pending()
    assert engine.interpreter.translation.accept_interpretation.call_count == fixture.ORIGINAL_COUNT
    assert case.clock.now < case.source.clock.now
    engine.interpreter.failures.record.assert_not_called()


def test_stop_preserves_tail_without_timer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Application stop retains pending input but does not start another work interval."""
    case = fixture.installed(tmp_path, monkeypatch)
    engine = case.engine
    stop = Event()
    before = case.pending()
    engine.interpreter.translation.accept_interpretation.side_effect = partial(fixture.stop_after_commit, stop)
    case.engine.queue.take.return_value = {WorkKind.RAW}
    case.engine.worker.run(stop)
    assert case.pending() == before[1:]
    case.engine.queue.set_deadline.assert_called_once_with(WorkKind.RAW, None, mixed_processing.CONTINUATION_KEY)
    case.engine.reactions.drain.assert_not_called()
    case.engine.inputs.close.assert_called_once()


def test_failed_commit_keeps_normal_retry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A repository exception is a failure, not a successful yield or an empty read."""
    case = fixture.installed(tmp_path, monkeypatch)
    engine = case.engine
    before = case.pending()
    monkeypatch.setattr(case.source.runtime.stores.facts, "record_interpretation", Mock(side_effect=RuntimeError))
    case.run()
    assert case.pending() == before
    case.engine.queue.schedule.assert_called_once_with(WorkKind.RAW, 1.0, key="retry")
    case.engine.queue.set_deadline.assert_not_called()
    engine.interpreter.translation.accept_interpretation.assert_not_called()
    case.engine.reactions.drain.assert_called_once()
