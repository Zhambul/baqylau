# Copyright (c) 2026 Zhambyl Yermagambet
"""Claude monitor event translation tests."""

from __future__ import annotations

from domain import (
    event_actor as actor_events,
    event_base,
    event_conversation as conversation_events,
    event_shell as shell_events,
    ids as domain_ids,
    outcomes,
)
from harness.impl.claude_code.canonical.translator import ClaudeCanonicalTranslator
from tests.harness_names import CLAUDE_CODE_HARNESS
from tests.plugin_tests import vocabulary as fixture
from tests.plugin_tests.support_events import payloads, raw_event
from tests.plugin_tests.support_hooks import armed_monitor, monitor_notification
from tests.plugin_tests.support_values import text_of


def test_claude_monitor_hook_accepts_structured() -> None:
    """Verify claude monitor hook accepts a structured reference response."""
    translator = ClaudeCanonicalTranslator()
    translated = translator.translate(
        raw_event(
            {
                fixture.HOOK_EVENT_NAME_FIELD: fixture.POST_TOOL_USE_HOOK,
                fixture.TOOL_USE_ID_FIELD: fixture.MONITOR_OP_ONE,
                fixture.TOOL_NAME_FIELD: fixture.MONITOR_TOOL,
                fixture.TOOL_INPUT_FIELD: {
                    fixture.COMMAND_FIELD: "tail -f log",
                    fixture.DESCRIPTION_FIELD: "ticks",
                },
                fixture.TOOL_RESPONSE_FIELD: [
                    {fixture.TYPE_FIELD: "tool_reference", fixture.TOOL_NAME_FIELD: fixture.MONITOR_TOOL},
                ],
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.HOOK_SOURCE,
            raw_event_id="structured-monitor-response",
        ),
    )

    assert payloads(translated, shell_events.ShellFinished)[0].payload.shell_id == domain_ids.ShellId(
        fixture.MONITOR_OP_ONE,
    )


def test_claude_rejected_monitor_has_no_running() -> None:
    """Verify claude rejected monitor has no running task to wait for."""
    translated = ClaudeCanonicalTranslator().translate(
        raw_event(
            {
                fixture.HOOK_EVENT_NAME_FIELD: "PostToolUseFailure",
                fixture.TOOL_USE_ID_FIELD: "monitor-rejected",
                fixture.TOOL_NAME_FIELD: fixture.MONITOR_TOOL,
                fixture.TOOL_INPUT_FIELD: {fixture.TASK_ID: "wrong-shape"},
                fixture.ERROR: "InputValidationError",
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.HOOK_SOURCE,
            raw_event_id="monitor-rejected-result",
        ),
    )

    assert payloads(translated, shell_events.ShellFinished)[0].payload.outcome == outcomes.Outcome.FAILED
    output_finished = payloads(translated, shell_events.ShellOutputFinished)[0].payload
    assert output_finished.shell_id == domain_ids.ShellId("monitor-rejected")
    assert output_finished.outcome == outcomes.Outcome.FAILED


def test_claude_agent_hook_does_not_hide_notice() -> None:
    """Verify claude agent hook does not hide the notification result."""
    translator = ClaudeCanonicalTranslator()
    returned = translator.translate(
        raw_event(
            {
                fixture.HOOK_EVENT_NAME_FIELD: fixture.POST_TOOL_USE_HOOK,
                fixture.TOOL_USE_ID_FIELD: fixture.AGENT_TOOL_ONE_ID,
                fixture.TOOL_NAME_FIELD: fixture.AGENT_TOOL,
                fixture.TOOL_INPUT_FIELD: {fixture.DESCRIPTION_FIELD: "ticker"},
                fixture.TOOL_RESPONSE_FIELD: [
                    {fixture.TYPE_FIELD: "tool_reference", fixture.TOOL_NAME_FIELD: fixture.AGENT_TOOL},
                ],
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.HOOK_SOURCE,
            raw_event_id="agent-returned",
        ),
    )
    completed = translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.USER,
                fixture.UUID_FIELD: "agent-notification",
                fixture.ORIGIN_FIELD: {fixture.KIND_FIELD: fixture.TASK_NOTIFICATION_ID},
                fixture.MESSAGE_FIELD: {
                    fixture.CONTENT_FIELD: (
                        "<task-notification><task-id>child-one</task-id>"
                        "<tool-use-id>agent-tool-one</tool-use-id>"
                        "<status>completed</status><summary>Agent ticker finished</summary>"
                        "<result>gathered</result></task-notification>"
                    ),
                },
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.TRANSCRIPT_SOURCE,
            raw_event_id="agent-notification",
        ),
    )

    assert returned.canonical_events == ()
    assignment = payloads(completed, actor_events.ActorAssignmentFinished)[0].payload
    assert text_of(assignment.result) == "gathered"


def test_claude_monitor_events_are_progress() -> None:
    """Verify claude monitor events are progress on the monitor not agent finishes.

    A monitor's events are the whole point of arming one, and every one of them
        was being read as an AGENT completing (session 246c8079, 2026-08-17): the
        <task-notification> fallback treated anything that was not a background
        command as an assignment finish, so six ticks became one phantom
        `actor.assignment_finished` — one, because they all carried an empty
        assignment id and collapsed onto a single event id — and the event text was
        dropped on the floor.
    """
    translator = ClaudeCanonicalTranslator()
    armed_monitor(translator)

    ticks = [
        translator.translate(
            monitor_notification(
                f"tick-{number}",
                f'<task-id>bmfwjr03l</task-id><summary>Monitor event: "ticks"</summary><event>tick-{number}</event>',
            ),
        )
        for number in (1, 2, 3)
    ]

    for tick in ticks:
        assert not payloads(tick, actor_events.ActorAssignmentFinished)
        assert not payloads(tick, conversation_events.MessageCreated)
    progressed = [payloads(translated_tick, shell_events.ShellProgressed)[0] for translated_tick in ticks]
    _assert_monitor_progress(progressed)


def _assert_monitor_progress(
    progressed: list[event_base.CanonicalEvent[shell_events.ShellProgressed]],
) -> None:
    """Verify the monitor progress events."""
    assert [text_of(entry.payload.content) for entry in progressed] == ["tick-1", "tick-2", "tick-3"]
    monitor_shell_id = domain_ids.ShellId(fixture.MONITOR_OP_ONE)
    assert {entry.payload.shell_id for entry in progressed} == {monitor_shell_id}
    # The "status" stream is what the monitors tab reads as an event rather than
    # as output, and the ordinals are what keep three events three rows: the
    # event id is built from the subject and the phase, so a shared phase would
    # collapse them the way the phantom assignment finishes collapsed.
    assert all(entry.payload.stream == fixture.STATUS_FIELD for entry in progressed)
    assert [entry.payload.ordinal for entry in progressed] == [0, 1, 2]
    assert len({entry.event_id for entry in progressed}) == len(progressed)
