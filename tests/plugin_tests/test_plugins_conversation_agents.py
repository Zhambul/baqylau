# Copyright (c) 2026 Zhambyl Yermagambet
"""Claude agent notification translation tests."""

from __future__ import annotations

from dataclasses import replace

from domain import (
    event_actor as actor_events,
    event_conversation as conversation_events,
    ids as domain_ids,
    outcomes,
)
from harness.impl.claude_code.canonical.translator import ClaudeCanonicalTranslator
from tests.harness_names import CLAUDE_CODE_HARNESS
from tests.plugin_tests import vocabulary as fixture
from tests.plugin_tests.support_events import payloads, raw_event
from tests.plugin_tests.support_values import text_of


def test_claude_async_agent_launch_stays_running() -> None:
    """Verify claude async agent launch stays running until task notification."""
    translator = ClaudeCanonicalTranslator()
    started = translator.translate(
        raw_event(
            {
                fixture.HOOK_EVENT_NAME_FIELD: fixture.PRE_TOOL_USE_HOOK,
                fixture.TOOL_USE_ID_FIELD: fixture.AGENT_TOOL_ONE_ID,
                fixture.TOOL_NAME_FIELD: fixture.AGENT_TOOL,
                fixture.TOOL_INPUT_FIELD: {
                    fixture.DESCRIPTION_FIELD: "Get current weather in Bali",
                    fixture.PROMPT_KIND: "Look up current weather and a short forecast.",
                    "subagent_type": "general-purpose",
                },
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.HOOK_SOURCE,
            raw_event_id="agent-start",
        ),
    )
    launch_ack = translator.translate(
        raw_event(
            {
                fixture.HOOK_EVENT_NAME_FIELD: fixture.POST_TOOL_USE_HOOK,
                fixture.TOOL_USE_ID_FIELD: fixture.AGENT_TOOL_ONE_ID,
                fixture.TOOL_NAME_FIELD: fixture.AGENT_TOOL,
                fixture.TOOL_INPUT_FIELD: {fixture.DESCRIPTION_FIELD: "Get current weather in Bali"},
                fixture.TOOL_RESPONSE_FIELD: {
                    "isAsync": True,
                    fixture.STATUS_FIELD: "async_launched",
                    "agentId": fixture.CHILD_ONE_ID,
                },
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.HOOK_SOURCE,
            raw_event_id="agent-launch-ack",
        ),
    )

    child_started = payloads(started, actor_events.ActorAssignmentStarted)[0].payload
    assert text_of(child_started.brief) == "Get current weather in Bali"
    assert child_started.actor_name == "general-purpose"
    assert text_of(child_started.prompt) == "Look up current weather and a short forecast."
    # An async launch's result finishes nothing: the Agent tool returned, the
    # assignment did not. There is no shell here either — an assignment is not a
    # command — so the whole delivery says only that.
    assert launch_ack.canonical_events == ()
    assert launch_ack.decision == fixture.IGNORED_NONSEMANTIC


def test_claude_team_idle_deduplicates_one_worker() -> None:
    """Verify claude team idle deduplicates one worker in one delivery."""
    translator = ClaudeCanonicalTranslator()
    translator.translate(
        raw_event(
            {
                fixture.HOOK_EVENT_NAME_FIELD: fixture.PRE_TOOL_USE_HOOK,
                fixture.TOOL_USE_ID_FIELD: fixture.AGENT_TOOL_ONE_ID,
                fixture.TOOL_NAME_FIELD: fixture.AGENT_TOOL,
                fixture.TOOL_INPUT_FIELD: {
                    fixture.DESCRIPTION_FIELD: "Check the state",
                    fixture.PROMPT_KIND: "Reply with DONE.",
                    fixture.NAME_FIELD: fixture.WORKER_ONE_ID,
                },
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.HOOK_SOURCE,
            raw_event_id="team-agent-start",
        ),
    )
    translator.translate(
        raw_event(
            {
                fixture.HOOK_EVENT_NAME_FIELD: fixture.POST_TOOL_USE_HOOK,
                fixture.TOOL_USE_ID_FIELD: fixture.AGENT_TOOL_ONE_ID,
                fixture.TOOL_NAME_FIELD: fixture.AGENT_TOOL,
                fixture.TOOL_INPUT_FIELD: {fixture.DESCRIPTION_FIELD: "Check the state"},
                fixture.TOOL_RESPONSE_FIELD: {
                    fixture.STATUS_FIELD: "teammate_spawned",
                    fixture.NAME_FIELD: fixture.WORKER_ONE_ID,
                },
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.HOOK_SOURCE,
            raw_event_id="team-agent-launched",
        ),
    )
    delivery = replace(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.USER,
                fixture.UUID_FIELD: "team-idle-delivery",
                fixture.MESSAGE_FIELD: {
                    fixture.CONTENT_FIELD: (
                        "Another Claude session sent a message:\n"
                        '<teammate-message teammate_id="worker-one">'
                        '{"type":"idle_notification","from":"worker-one",'
                        '"timestamp":"2026-08-25T00:00:00Z",'
                        '"idleReason":"available"}'
                        "</teammate-message>\n"
                        '<teammate-message teammate_id="worker-one">'
                        '{"type":"idle_notification","from":"worker-one",'
                        '"timestamp":"2026-08-25T00:00:01Z",'
                        '"idleReason":"available"}'
                        "</teammate-message>\n"
                        "This came from another Claude session."
                    ),
                },
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.TRANSCRIPT_SOURCE,
            raw_event_id="team-idle-delivery",
        ),
        actor_id=domain_ids.ActorId(fixture.WORKER_ONE_ID),
        parent_actor_id=domain_ids.ActorId(fixture.SESSION_ONE_LEAD_ID),
    )

    finished = payloads(translator.translate(delivery), actor_events.ActorAssignmentFinished)

    assert len(finished) == 1
    finished_event = finished[0]
    assert finished_event.payload.assignment_id == fixture.AGENT_TOOL_ONE_ID
    assert finished_event.payload.outcome == fixture.SUCCEEDED


def test_claude_task_notice_finishes_actor() -> None:
    """Verify claude task notification finishes actor assignment instead of creating user message."""
    notification = ClaudeCanonicalTranslator().translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.USER,
                fixture.UUID_FIELD: "task-notification-one",
                fixture.ORIGIN_FIELD: {fixture.KIND_FIELD: fixture.TASK_NOTIFICATION_ID},
                fixture.PROMPT_SOURCE_FIELD: fixture.SYSTEM,
                fixture.MESSAGE_FIELD: {
                    fixture.CONTENT_FIELD: (
                        "<task-notification><task-id>child-one</task-id>"
                        "<tool-use-id>agent-tool-one</tool-use-id>"
                        "<status>completed</status>"
                        '<summary>Agent "Get current weather in Bali" finished</summary>'
                        "<result>Sunny, 29°C.</result></task-notification>"
                    ),
                },
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.TRANSCRIPT_SOURCE,
            raw_event_id=fixture.TASK_NOTIFICATION_ID,
        ),
    )

    finished = payloads(notification, actor_events.ActorAssignmentFinished)
    assert not payloads(notification, conversation_events.MessageCreated)
    finished_event = finished[0]
    assert finished_event.payload.assignment_id == fixture.AGENT_TOOL_ONE_ID
    assert finished_event.payload.outcome == fixture.SUCCEEDED
    assert text_of(finished_event.payload.result) == "Sunny, 29°C."


def test_claude_killed_task_notice_cancels_actor() -> None:
    """Verify claude killed task notification cancels actor assignment."""
    notification = ClaudeCanonicalTranslator().translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.QUEUE_OPERATION_ID,
                fixture.OPERATION_FIELD: fixture.ENQUEUE,
                fixture.CONTENT_FIELD: (
                    "<task-notification><task-id>child-one</task-id>"
                    "<tool-use-id>agent-tool-one</tool-use-id>"
                    "<status>killed</status>"
                    '<summary>Agent "e2e_child_sleep" was stopped by Claude</summary>'
                    "</task-notification>"
                ),
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.TRANSCRIPT_SOURCE,
            raw_event_id="killed-task-notification",
        ),
    )

    finished = payloads(notification, actor_events.ActorAssignmentFinished)
    assert len(finished) == 1
    finished_event = finished[0]
    assert finished_event.payload.assignment_id == fixture.AGENT_TOOL_ONE_ID
    assert finished_event.payload.outcome == outcomes.Outcome.CANCELLED


def test_claude_resumed_async_child_finishes_its() -> None:
    """Verify claude resumed async child finishes its agent assignment."""
    translator = ClaudeCanonicalTranslator()
    translator.translate(
        raw_event(
            {
                fixture.HOOK_EVENT_NAME_FIELD: fixture.PRE_TOOL_USE_HOOK,
                fixture.TOOL_USE_ID_FIELD: fixture.AGENT_TOOL_ONE_ID,
                fixture.TOOL_NAME_FIELD: fixture.AGENT_TOOL,
                fixture.TOOL_INPUT_FIELD: {fixture.DESCRIPTION_FIELD: "Wait for follow-up"},
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.HOOK_SOURCE,
            raw_event_id="agent-start",
        ),
    )
    translator.translate(
        raw_event(
            {
                fixture.HOOK_EVENT_NAME_FIELD: fixture.POST_TOOL_USE_HOOK,
                fixture.TOOL_USE_ID_FIELD: fixture.AGENT_TOOL_ONE_ID,
                fixture.TOOL_NAME_FIELD: fixture.AGENT_TOOL,
                fixture.TOOL_INPUT_FIELD: {fixture.DESCRIPTION_FIELD: "Wait for follow-up"},
                fixture.TOOL_RESPONSE_FIELD: {
                    "isAsync": True,
                    fixture.STATUS_FIELD: "async_launched",
                    "agentId": fixture.CHILD_ONE_ID,
                },
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.HOOK_SOURCE,
            raw_event_id="agent-launch-ack",
        ),
    )
    notification = translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.USER,
                fixture.UUID_FIELD: fixture.RESUMED_TASK_NOTIFICATION_ID,
                fixture.ORIGIN_FIELD: {fixture.KIND_FIELD: fixture.TASK_NOTIFICATION_ID},
                fixture.MESSAGE_FIELD: {
                    fixture.CONTENT_FIELD: (
                        "<task-notification><task-id>child-one</task-id>"
                        "<tool-use-id>send-message-one</tool-use-id>"
                        "<status>completed</status>"
                        '<summary>Agent "Wait for follow-up" finished</summary>'
                        "<result>FOLLOWUP_MARKER_417</result></task-notification>"
                    ),
                },
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.TRANSCRIPT_SOURCE,
            raw_event_id=fixture.RESUMED_TASK_NOTIFICATION_ID,
        ),
    )

    finished = payloads(notification, actor_events.ActorAssignmentFinished)[0]
    assert finished.payload.assignment_id == fixture.AGENT_TOOL_ONE_ID
    assert text_of(finished.payload.result) == "FOLLOWUP_MARKER_417"
