# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep required Claude session state separate from changed or absent activity."""

from domain import event_actor, event_conversation, event_session
from harness.impl.claude_code.canonical.translator import ClaudeCanonicalTranslator
from harness.models import translation_stages as stages
from tests.plugin_tests import translation_stage_fixture as fixture
from tests.plugin_tests.support_events import payloads
from tests.plugin_tests.support_values import text_of

ORIGINAL_TEXT = "original"
STOP_HOOK = "Stop"


def test_required_prompt_does_not_open_a_turn() -> None:
    """A dropped prompt must not change the in-memory turn used by a later Stop."""
    translator = ClaudeCanonicalTranslator()
    required = translator.translate(
        fixture.claude_prompt(ORIGINAL_TEXT), translation_stage=stages.TranslationStage.LIFECYCLE,
    )
    stopped = translator.translate(fixture.claude_hook(STOP_HOOK), translation_stage=stages.TranslationStage.ACTIVITY)
    expected = ClaudeCanonicalTranslator().translate(
        fixture.claude_hook(STOP_HOOK), translation_stage=stages.TranslationStage.ACTIVITY,
    )
    assert tuple(type(event.payload) for event in required.canonical_events) == (
        event_session.SessionStarted, event_actor.ActorStarted,
    )
    assert all(stages.required_event(event) for event in required.canonical_events)
    assert stopped == expected


def test_activity_cannot_replace_required_start() -> None:
    """Use original start fields but only the changed prompt for activity."""
    translator = ClaudeCanonicalTranslator()
    required = translator.translate(
        fixture.claude_prompt(ORIGINAL_TEXT), translation_stage=stages.TranslationStage.LIFECYCLE,
    )
    activity = translator.translate(fixture.claude_prompt("changed", directory="/changed"),
                                    translation_stage=stages.TranslationStage.ACTIVITY)
    assert payloads(required, event_session.SessionStarted)[0].payload.working_directory == "/work"
    assert activity.canonical_events
    assert not any(stages.required_event(event) for event in activity.canonical_events)
    assert text_of(payloads(activity, event_conversation.MessageCreated)[0].payload.content) == "changed"


def test_added_session_finish_is_not_activity() -> None:
    """Added activity must not close a session or clear its open turn."""
    translator = ClaudeCanonicalTranslator()
    translator.translate(fixture.claude_prompt(ORIGINAL_TEXT), translation_stage=stages.TranslationStage.ACTIVITY)
    added = translator.translate(fixture.claude_hook("SessionEnd"), translation_stage=stages.TranslationStage.ACTIVITY)
    required = translator.translate(
        fixture.claude_hook("SessionEnd"), translation_stage=stages.TranslationStage.LIFECYCLE,
    )
    stopped = translator.translate(fixture.claude_hook(STOP_HOOK), translation_stage=stages.TranslationStage.ACTIVITY)
    assert added.canonical_events == ()
    assert len(payloads(required, event_session.SessionFinished)) == 1
    assert payloads(stopped, event_conversation.TurnFinished)[0].turn_id is not None


def test_release_remains_an_explicit_host_action() -> None:
    """A lifecycle read cannot substitute for post-acceptance memory release."""
    translator = ClaudeCanonicalTranslator()
    original = fixture.claude_prompt(ORIGINAL_TEXT)
    translator.translate(original, translation_stage=stages.TranslationStage.ACTIVITY)
    translator.translate(fixture.claude_hook("SessionEnd"), translation_stage=stages.TranslationStage.LIFECYCLE)
    translator.release_session(original.session_id)
    expected = ClaudeCanonicalTranslator().translate(
        fixture.claude_hook(STOP_HOOK), translation_stage=stages.TranslationStage.ACTIVITY,
    )
    stopped = translator.translate(fixture.claude_hook(STOP_HOOK), translation_stage=stages.TranslationStage.ACTIVITY)
    assert stopped == expected
