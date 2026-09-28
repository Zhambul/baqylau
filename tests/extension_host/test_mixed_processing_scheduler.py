# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep mixed continuations bounded, event-driven, and separate from other deadlines."""

from contextlib import nullcontext
from unittest.mock import ANY, Mock, call

import pytest

from core import work_queue
from engine import mixed_processing
from extensions.interpretation_contract import CoreInterpretation
from extensions.processing_contract import ExtensionProcessing, ExtensionProcessingBatch
from tests.extension_host import source_engine_fixture as engines, source_processing_fixture as sources

PAGE_COUNT = 100
OTHER_DELAY = 2.0
CLOCK_NAME = "monotonic"


@pytest.mark.parametrize("completed", [0, 1, PAGE_COUNT])
def test_one_mixed_page_precedes_reactions(monkeypatch: pytest.MonkeyPatch, completed: int) -> None:
    """A positive result cannot start an unbounded drain under the same runtime."""
    monkeypatch.setattr(mixed_processing, CLOCK_NAME, sources.SourceClock())
    batch = Mock(spec=ExtensionProcessingBatch, interpret_pending=Mock(side_effect=[completed, 0]))
    processing = Mock(spec=ExtensionProcessing, capture_batch=Mock(return_value=nullcontext(batch)))
    engine = engines.engine(monkeypatch, processing)
    trace = Mock()
    trace.attach_mock(batch.interpret_pending, "interpret")
    trace.attach_mock(engine.reactions.drain, "react")
    engine.run({work_queue.WorkKind.RAW})
    assert trace.mock_calls == [
        call.interpret(engine.interpreter.translation, ANY, yield_requested=ANY), call.react(ANY, None),
    ]
    delay = mixed_processing.CONTINUATION_SECONDS if completed else None
    engine.queue.set_deadline.assert_called_once_with(
        work_queue.WorkKind.RAW, delay, mixed_processing.CONTINUATION_KEY,
    )
    engine.interpreter.translation.translate.assert_not_called()
    processing.capture_batch.assert_called_once()


@pytest.mark.parametrize("elapsed", [-0.1, 0, 0.1])
def test_interval_includes_exact_deadline(monkeypatch: pytest.MonkeyPatch, elapsed: float) -> None:
    """The end of the interval requests a yield, not one more original."""
    clock = sources.SourceClock()
    monkeypatch.setattr(mixed_processing, CLOCK_NAME, clock)
    interval = mixed_processing.ProcessingSlice(clock.now)
    clock.now += elapsed
    assert interval.expired() == (elapsed >= 0)


def test_continuation_delivers_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """The real queue sends one raw continuation and stops after an empty read."""
    clock = sources.SourceClock()
    monkeypatch.setattr(work_queue, CLOCK_NAME, clock)
    monkeypatch.setattr(mixed_processing, CLOCK_NAME, clock)
    queue = work_queue.WorkQueue()
    batch = Mock(spec=ExtensionProcessingBatch, interpret_pending=Mock(side_effect=[1, 0]))
    mixed_processing.read_mixed(batch, Mock(spec=CoreInterpretation), queue, bool)
    clock.now += mixed_processing.CONTINUATION_SECONDS
    queue.put(work_queue.WorkKind.CANONICAL)
    assert queue.take() == {work_queue.WorkKind.RAW, work_queue.WorkKind.CANONICAL}
    mixed_processing.read_mixed(batch, Mock(spec=CoreInterpretation), queue, bool)
    clock.now += OTHER_DELAY
    queue.put(work_queue.WorkKind.CANONICAL)
    assert queue.take() == {work_queue.WorkKind.CANONICAL}


def test_empty_read_keeps_other_raw_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clearing the mixed continuation must not cancel another producer's retry."""
    clock = sources.SourceClock()
    monkeypatch.setattr(work_queue, CLOCK_NAME, clock)
    monkeypatch.setattr(mixed_processing, CLOCK_NAME, clock)
    queue = work_queue.WorkQueue()
    queue.schedule(work_queue.WorkKind.RAW, OTHER_DELAY, "other retry")
    queue.set_deadline(
        work_queue.WorkKind.RAW, mixed_processing.CONTINUATION_SECONDS, mixed_processing.CONTINUATION_KEY,
    )
    batch = Mock(spec=ExtensionProcessingBatch, interpret_pending=Mock(return_value=0))
    mixed_processing.read_mixed(batch, Mock(spec=CoreInterpretation), queue, bool)
    clock.now += mixed_processing.CONTINUATION_SECONDS
    queue.put(work_queue.WorkKind.CANONICAL)
    assert queue.take() == {work_queue.WorkKind.CANONICAL}
    clock.now += OTHER_DELAY
    queue.put(work_queue.WorkKind.CANONICAL)
    assert queue.take() == {work_queue.WorkKind.RAW, work_queue.WorkKind.CANONICAL}
