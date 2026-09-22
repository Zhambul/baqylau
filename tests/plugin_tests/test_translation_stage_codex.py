# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep required Codex starts out of compaction and tool memory."""

from pathlib import Path

from domain import event_session, event_telemetry
from harness.impl.codex.canonical.translator import CodexCanonicalTranslator
from harness.models import translation_stages as stages
from tests.plugin_tests import translation_stage_fixture as fixture
from tests.plugin_tests.support_events import payloads

START_FACT_COUNT = 2
CHANGED_TOKEN_COUNT = 12


def test_required_hook_does_not_record_compaction(tmp_path: Path) -> None:
    """A dropped compaction hook must not change a later compaction result."""
    translator = CodexCanonicalTranslator()
    required = translator.translate(
        fixture.codex_compaction(tmp_path), translation_stage=stages.TranslationStage.LIFECYCLE,
    )
    boundary = fixture.codex_boundary()
    actual = translator.translate(boundary, translation_stage=stages.TranslationStage.ACTIVITY)
    expected = CodexCanonicalTranslator().translate(boundary, translation_stage=stages.TranslationStage.ACTIVITY)
    assert len(required.canonical_events) == START_FACT_COUNT
    assert all(stages.required_event(event) for event in required.canonical_events)
    assert actual == expected
    assert payloads(actual, event_telemetry.CompactionFinished)[0].payload.before_tokens is None


def test_changed_hook_sets_only_activity(tmp_path: Path) -> None:
    """The original starts the session; selected content supplies compaction state."""
    translator = CodexCanonicalTranslator()
    required = translator.translate(
        fixture.codex_compaction(tmp_path), translation_stage=stages.TranslationStage.LIFECYCLE,
    )
    changed = translator.translate(
        fixture.codex_compaction(tmp_path, before=CHANGED_TOKEN_COUNT),
        translation_stage=stages.TranslationStage.ACTIVITY,
    )
    boundary = fixture.codex_boundary()
    finished = translator.translate(boundary, translation_stage=stages.TranslationStage.ACTIVITY)
    assert payloads(required, event_session.SessionStarted)[0].payload.working_directory == "/work"
    assert len(changed.canonical_events) == 1
    assert not any(stages.required_event(event) for event in changed.canonical_events)
    assert payloads(finished, event_telemetry.CompactionFinished)[0].payload.before_tokens == CHANGED_TOKEN_COUNT


def test_added_metadata_cannot_start_a_session() -> None:
    """Session metadata is inert in the activity pass."""
    original = fixture.codex_record({"type": "session_meta", "payload": {"cwd": "/work"}}, position="0")
    translator = CodexCanonicalTranslator()
    assert translator.translate(original, translation_stage=stages.TranslationStage.ACTIVITY).canonical_events == ()
    required = translator.translate(original, translation_stage=stages.TranslationStage.LIFECYCLE)
    assert len(required.canonical_events) == START_FACT_COUNT


def test_lifecycle_pass_does_not_decode_tools() -> None:
    """Valid record metadata does not require an activity decoder in this pass."""
    original = fixture.codex_record({"type": "response_item", "payload": {"type": "function_call", "arguments": "{"}})
    translated = CodexCanonicalTranslator().translate(original, translation_stage=stages.TranslationStage.LIFECYCLE)
    assert translated.canonical_events == ()
