# Copyright (c) 2026 Zhambyl Yermagambet
"""Claude model and effort picker output tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from domain import event_session
from harness.impl.claude_code.canonical.translator import ClaudeCanonicalTranslator
from tests.harness_names import CLAUDE_CODE_HARNESS
from tests.plugin_tests import vocabulary as fixture
from tests.plugin_tests.support_events import payloads, raw_event

if TYPE_CHECKING:
    from harness.models import raw_events


def _command_output_translation(output: str) -> raw_events.TranslationResult:
    """Translate one local command output record.

    Returns:
        The translation of the wrapped output text.

    """
    return ClaudeCanonicalTranslator().translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.USER,
                fixture.UUID_FIELD: "command-output",
                fixture.MESSAGE_FIELD: {
                    fixture.CONTENT_FIELD: f"<local-command-stdout>{output}</local-command-stdout>",
                },
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.TRANSCRIPT_SOURCE,
            raw_event_id="slash-command-output",
        ),
    )


@pytest.mark.parametrize(
    ("output", "model_name"),
    [
        ("Set model to Sonnet 5 and saved as your default for new sessions", "sonnet"),
        ("Set model to `Fable 5.1` and saved as your default for new sessions", "fable"),
        ("Set model to `Opus 5 (1M context)` and saved as your default for new sessions", "opus"),
        ("Set model to \x1b[1mSonnet 5\x1b[22m and saved as your default for new sessions", "sonnet"),
        ("Set model to `claude-fable-5-1[1m]` and saved as your default for new sessions", "fable"),
    ],
)
def test_claude_model_picker_reports_selection(output: str, model_name: str) -> None:
    """Verify the model picker output reports the chosen alias."""
    models = [payload.payload for payload in payloads(_command_output_translation(output), event_session.ModelChanged)]
    assert len(models) == 1
    assert models[0].current.name == model_name
    assert models[0].reason == "selected"


@pytest.mark.parametrize("level", ["medium", "xhigh"])
def test_claude_effort_picker_reports_selection(level: str) -> None:
    """Verify the effort picker output reports the chosen level."""
    output = f"Set effort level to {level} (saved as your default for new sessions): Balanced"
    translated = _command_output_translation(output)
    efforts = [payload.payload for payload in payloads(translated, event_session.EffortChanged)]
    assert len(efforts) == 1
    assert efforts[0].current == level
    assert efforts[0].reason == "selected"


def test_claude_other_command_output_settles_no() -> None:
    """Verify command output without a selection settles no state."""
    translation = _command_output_translation("Session renamed to: another title")
    assert translation.canonical_events == ()
