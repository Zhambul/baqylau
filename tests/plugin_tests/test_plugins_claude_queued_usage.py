# Copyright (c) 2026 Zhambyl Yermagambet
"""A queued Claude task notification that reports its cost translates."""

from typing import TYPE_CHECKING

from harness.impl.claude_code.canonical.translator import ClaudeCanonicalTranslator
from tests.harness_names import CLAUDE_CODE_HARNESS
from tests.plugin_tests import vocabulary as fixture
from tests.plugin_tests.support_events import raw_event

if TYPE_CHECKING:
    from tests.plugin_tests.support_values import JsonValue


def test_queued_command_with_usage_translates() -> None:
    """Claude Code 2.1 adds the tokens and the duration to a queued command."""
    attachment: JsonValue = {
        fixture.TYPE_FIELD: "queued_command",
        "prompt": "<task-notification><task-id>a1</task-id><status>completed</status></task-notification>",
        "usage": {"totalTokens": 11214, "durationMs": 1833},
    }
    ClaudeCanonicalTranslator().translate(
        raw_event(
            {fixture.TYPE_FIELD: "attachment", "attachment": attachment},
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.TRANSCRIPT_SOURCE,
            raw_event_id="queued-usage",
        ),
    )
