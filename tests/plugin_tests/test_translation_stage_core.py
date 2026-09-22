# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep host session starts and finishes separate from core activity."""

import pytest

from engine.interpret import translators
from harness.contract import CoreTranslator
from harness.models import raw_events, translation_stages as stages
from tests.plugin_tests import translation_stage_fixture as fixture

REQUIRED_INPUTS: tuple[tuple[CoreTranslator, raw_events.RawEvent], ...] = (
    (translators.LivenessTranslator(), fixture.core_input({"process_id": 12, "state": "exited"}, "liveness")),
    (translators.ResumeLivenessTranslator(), fixture.core_input({}, "resume_liveness")),
    (translators.SessionResumeTranslator(), fixture.core_input({
        "working_directory": "/work", "source_reference": "/work/transcript",
    }, "resume_launch")),
    (translators.ControlTranslator(), fixture.core_input({"process_id": 12, "state": "exited"},
                                                        "control", name="session_finish")),
)


@pytest.mark.parametrize(("translator", "original"), REQUIRED_INPUTS)
def test_core_activity_cannot_change_lifecycle(
    translator: CoreTranslator, original: raw_events.RawEvent,
) -> None:
    """A core lifecycle input produces no events when used as derived activity."""
    complete = translator.translate(original)
    required = translator.translate(original, translation_stage=stages.TranslationStage.LIFECYCLE)
    activity = translator.translate(original, translation_stage=stages.TranslationStage.ACTIVITY)
    assert required == complete
    assert required.canonical_events
    assert all(stages.required_event(event) for event in required.canonical_events)
    assert activity.canonical_events == ()


def test_core_activity_is_not_required_state() -> None:
    """A turn interrupt is activity, not a session finish."""
    original = fixture.core_input({}, "interrupt")
    translator = translators.InterruptTranslator()
    assert translator.translate(original, translation_stage=stages.TranslationStage.LIFECYCLE).canonical_events == ()
    activity = translator.translate(original, translation_stage=stages.TranslationStage.ACTIVITY)
    assert activity == translator.translate(original)
