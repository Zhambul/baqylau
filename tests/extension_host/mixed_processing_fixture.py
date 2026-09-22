# Copyright (c) 2026 Zhambyl Yermagambet
"""Connect cooperative raw processing to actual source storage and registry reads."""

from dataclasses import dataclass
from pathlib import Path
from threading import Event

import pytest

from core.work_queue import WorkKind
from engine import mixed_processing
from extensions.models.interpretations import InterpretationOutcome
from extensions.models.observations import StoredObservation
from tests.extension_host import source_engine_fixture as engines, source_processing_fixture as sources

ORIGINAL_COUNT = 2
WALL_JUMP = 10_000


@dataclass(frozen=True)
class SliceCase:
    """Keep elapsed time separate from the source's wall clock."""

    source: sources.SourceCase
    engine: engines.EngineCase
    clock: sources.SourceClock

    def run(self) -> None:
        """Run one raw notice and its dependent core reactions."""
        self.engine.run({WorkKind.RAW})

    def pending(self) -> tuple[StoredObservation, ...]:
        """Read actual pending originals.

        Returns:
            The remaining input in arrival order.

        """
        original = self.source.original.original
        return original.store.pending_observations(100)

    def expire_after_commit(self, original: StoredObservation, outcome: InterpretationOutcome) -> None:
        """Require a complete journal and retained runtime before ending the interval."""
        self._accepted(original, outcome)
        self.clock.now += mixed_processing.MIXED_SECONDS

    def change_wall_time(self, original: StoredObservation, outcome: InterpretationOutcome) -> None:
        """Change wall time without advancing the monotonic interval."""
        self._accepted(original, outcome)
        self.source.clock.now += WALL_JUMP

    def _accepted(self, original: StoredObservation, outcome: InterpretationOutcome) -> None:
        engines.require_busy(self.source)
        stores = self.source.runtime.stores
        stored = stores.facts.find_interpretation("default", original.observation.raw_event_id)
        assert stored is not None
        assert outcome.accepted or outcome.deduplicated


def installed(path: Path, monkeypatch: pytest.MonkeyPatch) -> SliceCase:
    """Store two source originals before starting mixed raw work.

    Returns:
        The actual engine and stores around controlled source and core calls.

    """
    source = sources.installed(path)
    source.probe.behavior.pages = ORIGINAL_COUNT
    source.run()
    clock = sources.SourceClock()
    monkeypatch.setattr(mixed_processing, "monotonic", clock)
    return SliceCase(source, engines.engine(monkeypatch, source.runtime), clock)


def stop_after_commit(stop: Event, original: StoredObservation, outcome: InterpretationOutcome) -> None:
    """Stop only after actual interpretation acceptance."""
    assert original.cursor > 0 and (outcome.accepted or outcome.deduplicated)
    stop.set()
