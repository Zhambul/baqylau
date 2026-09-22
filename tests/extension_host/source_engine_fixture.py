# Copyright (c) 2026 Zhambyl Yermagambet
"""Connect the real engine stage order to controlled core consumers."""

from collections.abc import Callable
from dataclasses import dataclass
from threading import Event
from unittest.mock import Mock

import pytest

from core import input_events, work_queue
from engine import source_processing, worker
from engine.interpret.loop import Interpreter
from engine.react.loop import ReactionLoop
from extensions import processing_contract, registry_snapshot
from extensions.models import interpretations, observations
from tests.extension_host import registry_memory_fixture, source_processing_fixture


@dataclass(frozen=True)
class EngineCase:
    """Use real stage code with no native handles or live harness processes."""

    worker: worker.EngineWorker
    interpreter: Mock
    reactions: Mock
    inputs: Mock
    queue: Mock

    def run(self, pending: set[work_queue.WorkKind]) -> None:
        """Return one explicit work notice and then close the controlled input queue."""
        self.queue.take.side_effect = [pending, set()]
        self.worker.run(Event())


def engine(
    monkeypatch: pytest.MonkeyPatch, processing: processing_contract.ExtensionProcessing | None = None,
) -> EngineCase:
    """Replace native resources before construction so no unstarted descriptors leak.

    Returns:
        The actual engine and its explicit test boundaries.

    """
    interpreter = _interpreter()
    reactions = Mock(spec=ReactionLoop)
    inputs = Mock(spec=input_events.InputEvents)
    queue = Mock(spec=work_queue.WorkQueue)
    monkeypatch.setattr("engine.worker.InputEvents", Mock(return_value=inputs))
    return EngineCase(worker.EngineWorker(
        interpreter, reactions, queue, (), source_processing.EngineExtensionServices(processing=processing),
    ), interpreter, reactions, inputs, queue)


def _interpreter() -> Mock:
    interpreter = Mock(spec=Interpreter)
    interpreter.puller = Mock()
    interpreter.translation = Mock(translate=Mock(return_value=0))
    interpreter.failures = Mock()
    repositories = Mock()
    repositories.sessions.watchable.return_value = ()
    repositories.shell_output.oldest_created_at.return_value = None
    runtime = Mock(clock=Mock(return_value=1000))
    interpreter.dependencies = Mock(repositories=repositories, runtime=runtime)
    return interpreter


def require_busy(source: source_processing_fixture.SourceCase, stopped: Callable[[], bool] | None = None) -> int:
    """Require the registry read during source, interpretation, or reaction work.

    Returns:
        Zero for the controlled engine step.

    """
    assert stopped is None or not stopped()
    registry = source.runtime.services.registry
    replacement = registry_snapshot.prepare_snapshot(100, "replacement", ())
    assert registry.publish_snapshot(1, replacement, registry_memory_fixture.MEMORY_COMMIT).status == "busy"
    return 0


def accepted_busy(
    source: source_processing_fixture.SourceCase,
    original: observations.StoredObservation, outcome: interpretations.InterpretationOutcome,
) -> None:
    """Require fact acceptance before the core callback, with publication still held."""
    require_busy(source)
    assert source.runtime.stores.facts.find_interpretation("default", original.observation.raw_event_id) is not None
    assert outcome.accepted
