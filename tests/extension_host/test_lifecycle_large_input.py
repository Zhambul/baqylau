# Copyright (c) 2026 Zhambyl Yermagambet
"""Process required native state even when activity exceeds the worker byte limit."""

from dataclasses import replace
from pathlib import Path

import pytest
from baqylau_extension_api.core import actors, sessions
from baqylau_extension_api.models.content import MAX_CONTENT_BYTES

from extensions.models import interpretation_steps as steps
from harness.models.translation_stages import TranslationStage
from tests.extension_host import lifecycle_journal_changes as changes, lifecycle_pipeline_fixture as fixture
from tests.plugin_tests import translation_stage_fixture as native


def test_large_first_prompt_keeps_required_facts(tmp_path: Path) -> None:
    """Do not encode large worker content before the required start is accepted."""
    original = native.claude_prompt("x" * MAX_CONTENT_BYTES)
    case = fixture.installed(tmp_path, original)
    pipeline = case.pipeline
    core = case.core
    assert pipeline.run_batch() == 1
    stored = pipeline.original.store.find_interpretation("default", original.raw_event_id)
    assert stored is not None
    assert tuple(step.stage for step in stored.proposal.steps) == ("core_lifecycle", "unavailable_input")
    assert tuple(
        type(fact.payload) for fact in changes.required(stored).facts
    ) == (sessions.SessionStarted, actors.ActorStarted)
    assert pipeline.original.original.store.find_observation(original.raw_event_id) == pipeline.stored
    core.plugin.translator.translate.assert_called_once_with(
        pipeline.stored.observation, translation_stage=TranslationStage.LIFECYCLE,
    )
    pipeline.probes.raw.transform.assert_not_called()
    pipeline.probes.canonical.transform.assert_not_called()
    pipeline.probes.decoder.translate.assert_not_called()
    core.cache.invalidate.assert_called_once()


@pytest.mark.parametrize("valid", [True, False])
def test_large_finish_does_not_skip_cleanup(tmp_path: Path, *, valid: bool) -> None:
    """Valid finish input closes memory; invalid original bytes stay stored with a failure."""
    original = native.claude_hook("SessionEnd")
    original = replace(original, payload=(original.payload + b" " * MAX_CONTENT_BYTES) if valid else (
        b"x" * (MAX_CONTENT_BYTES + 1)
    ))
    case = fixture.installed(tmp_path, original)
    plugin = case.core.plugin
    assert case.pipeline.run_batch() == 1
    stored = case.pipeline.original.store.find_interpretation("default", original.raw_event_id)
    assert stored is not None
    required = stored.proposal.steps[0]
    assert isinstance(required, steps.CoreLifecycleStep)
    assert bool(required.facts) == valid
    case.require_original()
    if valid:
        plugin.translator.release_session.assert_called_once_with(original.session_id)
        plugin.sources.release_session.assert_called_once_with(original.session_id)
    else:
        plugin.translator.release_session.assert_not_called()
        plugin.sources.release_session.assert_not_called()
