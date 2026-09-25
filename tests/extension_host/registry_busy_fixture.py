# Copyright (c) 2026 Zhambyl Yermagambet
"""Require that the registry read stays held while the engine does source, interpretation, or reaction work."""

from collections.abc import Callable

from engine.sessiondata.contract import CoreChangeTransform
from extensions import registry_snapshot
from extensions.models import interpretations, observations
from tests import storage_reads
from tests.extension_host import registry_memory_fixture, source_processing_fixture


def require_busy(
    source: source_processing_fixture.SourceCase,
    stopped: Callable[[], bool] | None = None,
    core_transform: CoreChangeTransform | None = None,
) -> int:
    """Require the registry read during source, interpretation, or reaction work.

    A reaction drain also receives the batch's core transform, which this check does not use.

    Returns:
        Zero for the controlled engine step.

    """
    assert stopped is None or not stopped()
    assert core_transform is None or callable(core_transform.transform)
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
    assert storage_reads.find_interpretation(
        source.runtime.stores.facts, "default", original.observation.raw_event_id,
    ) is not None
    assert outcome.accepted
