# Copyright (c) 2026 Zhambyl Yermagambet
"""Claude native tool translation tests."""

from domain.event_shell import ShellStarted
from harness.impl.claude_code.canonical.translator import ClaudeCanonicalTranslator
from tests.harness_names import CLAUDE_CODE_HARNESS
from tests.plugin_tests import vocabulary as fixture
from tests.plugin_tests.support_events import payloads, raw_event
from tests.plugin_tests.support_values import text_of


def test_claude_unmapped_tool_stays_ignored_not() -> None:
    """Verify claude unmapped tool stays ignored not failed.

    The distinction the owner's decision draws, on THIS package's own
        "unknown kind" dispatch (toolcalls.TOOL_KINDS, not records.py): an
        unrecognised NATIVE TOOL NAME is that vocabulary growing — a Claude Code
        build shipping a tool this codebase has not mapped yet — not a shape
        mismatch within a tool it claims to know, so it stays `ignored_unknown`,
        never `translation_failed`, however ordinary its arguments look.
    """
    translated = ClaudeCanonicalTranslator().translate(
        raw_event(
            {
                fixture.HOOK_EVENT_NAME_FIELD: fixture.PRE_TOOL_USE_HOOK,
                fixture.SESSION_ID_FIELD: fixture.SESSION_ONE_ID,
                fixture.TOOL_USE_ID_FIELD: fixture.CALL_ONE_ID,
                fixture.TOOL_NAME_FIELD: "ATool2026HasNotShippedYet",
                fixture.TOOL_INPUT_FIELD: {"whatever": "fields"},
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.HOOK_SOURCE,
            raw_event_id="claude-unknown-kind",
        ),
    )
    assert translated.decision == fixture.IGNORED_UNKNOWN
    assert translated.canonical_events == ()


def test_claude_operation_execution_comes() -> None:
    """Verify claude operation execution comes from native tool semantics."""
    background = ClaudeCanonicalTranslator().translate(
        raw_event(
            {
                fixture.HOOK_EVENT_NAME_FIELD: fixture.PRE_TOOL_USE_HOOK,
                fixture.TOOL_USE_ID_FIELD: fixture.BACKGROUND_ONE,
                fixture.TOOL_NAME_FIELD: fixture.BASH_TOOL,
                fixture.TOOL_INPUT_FIELD: {
                    fixture.COMMAND_FIELD: "make test",
                    fixture.RUN_IN_BACKGROUND_FIELD: True,
                    fixture.DESCRIPTION_FIELD: fixture.RUN_TESTS_TEXT,
                },
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.HOOK_SOURCE,
            raw_event_id="background",
        ),
    )
    monitor = ClaudeCanonicalTranslator().translate(
        raw_event(
            {
                fixture.HOOK_EVENT_NAME_FIELD: fixture.PRE_TOOL_USE_HOOK,
                fixture.TOOL_USE_ID_FIELD: "monitor-one",
                fixture.TOOL_NAME_FIELD: fixture.MONITOR_TOOL,
                fixture.TOOL_INPUT_FIELD: {fixture.TASK_ID: "task-one"},
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.HOOK_SOURCE,
            raw_event_id="monitor",
        ),
    )

    assert payloads(background, ShellStarted)[0].payload.execution == "background"
    assert payloads(background, ShellStarted)[0].payload.description == fixture.RUN_TESTS_TEXT
    assert text_of(payloads(background, ShellStarted)[0].payload.command) == "make test"
    assert payloads(monitor, ShellStarted)[0].payload.execution == "monitor"
