# Copyright (c) 2026 Zhambyl Yermagambet
"""Check that the engine drains work, then waits."""

from __future__ import annotations

from contextlib import ExitStack
from threading import Event, Thread
from unittest.mock import Mock

import pytest

from core.work_queue import WorkKind, WorkQueue
from engine.interpret.loop import Interpreter
from engine.react.loop import ReactionLoop
from engine.worker import EngineWorker

IDLE_CHECK_SECONDS = 0.7
TRANSLATION_BATCHES = (500, 500, 1, 0)


class DrainObservedQueue(WorkQueue):
    """Report when the worker waits after its first reaction batch."""

    def __init__(self, reactions: Mock) -> None:
        """Connect the reaction probe to a normal work queue."""
        super().__init__()
        self.reactions = reactions
        self.drained = Event()

    def take(self) -> set[WorkKind]:
        """Report the idle boundary, then use the normal blocking wait.

        Returns:
            Pending work kinds, or an empty set after closure.

        """
        if self.reactions.drain.call_count == 1:
            self.drained.set()
        return super().take()


@pytest.fixture
def interpreter() -> Mock:
    """Build an interpreter probe with four translation batches.

    Returns:
        The interpreter probe with no output expiry deadline.

    """
    probe = Mock(spec=Interpreter)
    probe.translation = Mock()
    probe.puller = Mock()
    probe.failures = Mock()
    repositories = Mock()
    repositories.shell_output.oldest_created_at.return_value = None
    probe.dependencies = Mock(repositories=repositories)
    probe.translation.translate.side_effect = TRANSLATION_BATCHES
    return probe


@pytest.fixture
def reactions() -> Mock:
    """Build the reaction probe.

    Returns:
        The reaction probe.

    """
    return Mock(spec=ReactionLoop)


@pytest.fixture
def work_queue(reactions: Mock) -> DrainObservedQueue:
    """Build a queue that reports the idle boundary.

    Returns:
        The queue connected to the reaction probe.

    """
    return DrainObservedQueue(reactions)


def test_engine_drains_then_waits(
    monkeypatch: pytest.MonkeyPatch,
    interpreter: Mock,
    reactions: Mock,
    work_queue: DrainObservedQueue,
) -> None:
    """One notice drains all batches; idle time does not run either stage."""
    engine = EngineWorker(interpreter, reactions, work_queue, ())
    engine.inputs = Mock()
    monkeypatch.setattr(engine, "_subscriptions", Mock())
    stop = Event()
    worker = Thread(target=engine.run, args=(stop,))
    worker.start()
    with ExitStack() as cleanup:
        cleanup.callback(worker.join, 2)
        cleanup.callback(engine.stop)
        cleanup.callback(stop.set)
        assert work_queue.drained.wait(2)
        assert not stop.wait(IDLE_CHECK_SECONDS)
        assert interpreter.read_sources.call_count == 1
        assert interpreter.translation.translate.call_count == len(TRANSLATION_BATCHES)
        reactions.drain.assert_called_once_with(stop.is_set, None)
    assert not worker.is_alive()


@pytest.mark.parametrize("notice", [WorkKind.SOURCES, WorkKind.RAW])
def test_ready_data_reaches_display_in_one_pass(
    monkeypatch: pytest.MonkeyPatch,
    interpreter: Mock,
    reactions: Mock,
    work_queue: DrainObservedQueue,
    notice: WorkKind,
) -> None:
    """Do not put another source scan between new data and its display."""
    engine = EngineWorker(interpreter, reactions, work_queue, ())
    engine.inputs = Mock()
    monkeypatch.setattr(engine, "_subscriptions", Mock())
    monkeypatch.setattr(work_queue, "take", Mock(side_effect=[{notice}, set()]))
    engine.run(Event())
    assert interpreter.translation.translate.call_count == len(TRANSLATION_BATCHES)
    reactions.drain.assert_called_once()
