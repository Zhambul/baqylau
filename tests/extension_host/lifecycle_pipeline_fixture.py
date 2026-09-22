# Copyright (c) 2026 Zhambyl Yermagambet
"""Use actual native decoding and storage with controlled extension calls."""

from dataclasses import dataclass, replace
from pathlib import Path
from unittest.mock import Mock

from extensions.interpretation_contract import CoreInterpretation
from extensions.models import interpretations
from harness.contract import HarnessTranslator
from harness.impl.claude_code.canonical.translator import ClaudeCanonicalTranslator
from harness.models.raw_events import RawEvent
from repository.impl.sqlite.raw_events import SqliteRawEventRepository
from tests.extension_host import (
    interpretation_fixture as storage,
    interpretation_transforms as declarations,
    processing_core_fixture as core,
    processing_pipeline_fixture as pipeline,
)


@dataclass(frozen=True)
class LifecycleCase:
    """Keep the pure pipeline and post-acceptance core calls observable."""

    pipeline: pipeline.PipelineCase
    core: core.CoreCase

    def require_original(self) -> None:
        """Check that processing did not change the stored original."""
        original = self.pipeline.stored
        store = self.pipeline.original.original.store
        assert store.find_observation(original.observation.raw_event_id) == original


def phase() -> core.CoreCase:
    """Select the actual Claude translator without any native process or live source.

    Returns:
        The real core phase with observed reactions and memory-release calls.

    """
    selected = core.phase()
    selected.plugin.translator = Mock(spec=HarnessTranslator, wraps=ClaudeCanonicalTranslator())
    return selected


def installed(
    path: Path, raw_event: RawEvent, admission_limit: int = interpretations.MAX_INTERPRETATION_BYTES,
) -> LifecycleCase:
    """Record one core original under a real active extension declaration.

    Returns:
        A private database, native decoder, and selected no-op transforms.

    """
    original = storage.installed(path, declarations.combined_manifest())
    SqliteRawEventRepository(original.store.database).record((raw_event,))
    stored = original.original.store.find_observation(raw_event.raw_event_id)
    assert stored is not None
    selected = phase()
    probes = replace(pipeline.probes(), core=Mock(
        spec=CoreInterpretation, wraps=selected.phase,
    ))
    return LifecycleCase(pipeline.PipelineCase(original, stored, probes, admission_limit), selected)
