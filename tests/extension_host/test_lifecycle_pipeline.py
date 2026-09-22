# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep native lifecycle facts separate through the real processing batch."""

from pathlib import Path

from baqylau_extension_api.core import actors, sessions

from extensions.models import interpretation_steps as steps
from harness.models.translation_stages import TranslationStage
from tests.extension_host import lifecycle_journal_changes as changes, lifecycle_pipeline_fixture as fixture
from tests.plugin_tests import translation_stage_fixture as native

ORIGINAL_TEXT = "original"


def test_first_prompt_records_separate_passes(tmp_path: Path) -> None:
    """A first prompt records lifecycle before activity without an early reaction."""
    case = fixture.installed(tmp_path, native.claude_prompt(ORIGINAL_TEXT))
    translator = case.core.plugin.translator
    result = case.pipeline.run()
    required, activity = result.proposal.steps
    assert isinstance(required, steps.CoreLifecycleStep)
    assert isinstance(activity, steps.CoreActivityStep)
    assert tuple(
        type(fact.payload) for fact in required.facts
    ) == (sessions.SessionStarted, actors.ActorStarted)
    assert result.proposal.facts == (*required.facts, *activity.facts)
    assert tuple(call.kwargs["translation_stage"] for call in translator.translate.call_args_list) == (
        TranslationStage.LIFECYCLE, TranslationStage.ACTIVITY,
    )
    case.core.reaction.react.assert_not_called()
    case.core.canonical.record_translation.assert_not_called()


def test_required_facts_react_once_after_commit(tmp_path: Path) -> None:
    """The batch reacts to accepted facts once and then has no pending input."""
    case = fixture.installed(tmp_path, native.claude_prompt(ORIGINAL_TEXT))
    assert case.pipeline.run_batch() == 1
    accepted = case.pipeline.original.store.current_fact_page(0, 10).facts
    assert len(accepted) == case.core.reaction.react.call_count
    assert all(
        call.args[0].cursor > 0 for call in case.core.reaction.react.call_args_list
    )
    case.core.cache.invalidate.assert_called_once()
    assert case.pipeline.run_batch() == 0
    assert len(accepted) == case.core.reaction.react.call_count


def test_wrong_activity_group_keeps_start(tmp_path: Path) -> None:
    """A broken harness activity reply cannot insert a second required start."""
    case = fixture.installed(tmp_path, native.claude_prompt(ORIGINAL_TEXT))
    translator = case.core.plugin.translator
    required = translator.translate(
        case.pipeline.stored.observation, translation_stage=TranslationStage.LIFECYCLE,
    )
    translator.translate.return_value = required
    result = case.pipeline.run()
    assert result.proposal.facts == changes.required(result).facts
    activity = result.proposal.steps[1]
    assert isinstance(activity, steps.CoreActivityStep)
    assert activity.decision == "translation_failed"
    assert not activity.facts
    assert activity.reason is not None and "outside its selected pass" in activity.reason
