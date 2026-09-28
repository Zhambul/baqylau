# Copyright (c) 2026 Zhambyl Yermagambet
"""A Claude local command record that names its command translates."""

from harness.impl.claude_code.canonical.translator import ClaudeCanonicalTranslator
from tests.harness_names import CLAUDE_CODE_HARNESS
from tests.plugin_tests import vocabulary as fixture
from tests.plugin_tests.support_events import raw_event


def test_local_command_run_translates() -> None:
    """Claude Code 2.1 names the local command beside its output."""
    ClaudeCanonicalTranslator().translate(
        raw_event(
            {
                fixture.TYPE_FIELD: "system",
                "subtype": "local_command",
                fixture.CONTENT_FIELD: "<local-command-stdout>Session renamed to: A title</local-command-stdout>",
                "commandRun": {"command": "rename", "args": "A title"},
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.TRANSCRIPT_SOURCE,
            raw_event_id="local-command-run",
        ),
    )
