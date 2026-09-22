# Copyright (c) 2026 Zhambyl Yermagambet
"""Check the split protocol against saved native OpenCode2 streams."""

from pathlib import Path

import pytest

from domain import ids
from harness.impl.opencode2.sources import OpenCodeSource
from harness.impl.opencode2.translator import OpenCodeTranslator
from harness.models import raw_events, translation_stages as stages


@pytest.mark.parametrize("filename", [
    "audit_opencode2_greeting.jsonl", "audit_opencode2_child_question.jsonl",
    "audit_opencode2_background.jsonl", "audit_opencode2_files.jsonl",
])
def test_saved_records_have_disjoint_stages(filename: str) -> None:
    """The two passes retain every original fact, with no required fact in activity."""
    source = OpenCodeSource(raw_events.RawEventSourceContext(
        session_id=ids.SessionId("session-one"), lead_actor_id=ids.ActorId("session-one:lead"),
        actor_id=ids.ActorId("session-one:lead"), parent_actor_id=None,
        source_reference=str(Path(__file__).parents[1] / "e2e/fixtures" / filename),
    ))
    originals = source.read(None)
    assert originals
    for original in originals:
        _require_split(original)


def _require_split(original: raw_events.RawEvent) -> None:
    translator = OpenCodeTranslator()
    complete = translator.translate(original)
    required = translator.translate(original, translation_stage=stages.TranslationStage.LIFECYCLE)
    activity = translator.translate(original, translation_stage=stages.TranslationStage.ACTIVITY)
    assert all(stages.required_event(event) for event in required.canonical_events)
    assert not any(stages.required_event(event) for event in activity.canonical_events)
    assert (*required.canonical_events, *activity.canonical_events) == complete.canonical_events
