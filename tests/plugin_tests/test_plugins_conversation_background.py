# Copyright (c) 2026 Zhambyl Yermagambet
"""Claude background shell translation tests."""

from __future__ import annotations

from domain import (
    event_actor as actor_events,
    event_conversation as conversation_events,
    event_shell as shell_events,
    ids as domain_ids,
)
from harness.impl.claude_code.canonical.translator import ClaudeCanonicalTranslator
from tests.harness_names import CLAUDE_CODE_HARNESS
from tests.plugin_tests import vocabulary as fixture
from tests.plugin_tests.conversation_support import (
    claude_background_outcome,
)
from tests.plugin_tests.support_events import payloads, raw_event


def test_claude_bg_completion_is_output_finish() -> None:
    """Verify claude background completion is an output finish not an agent finish.

    Background Bash completions ride the SAME <task-notification> channel as
        agent completions; treating them as assignment finishes painted phantom
        "Agent finished" blocks for plain background commands (session 67dfd402,
        2026-08-16). The summary prefix is the discriminator.
    """
    notification = ClaudeCanonicalTranslator().translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.USER,
                fixture.UUID_FIELD: "background-completion-one",
                fixture.ORIGIN_FIELD: {fixture.KIND_FIELD: fixture.TASK_NOTIFICATION_ID},
                fixture.PROMPT_SOURCE_FIELD: fixture.SYSTEM,
                fixture.MESSAGE_FIELD: {
                    fixture.CONTENT_FIELD: (
                        "<task-notification><task-id>bkdr7jbeo</task-id>"
                        "<tool-use-id>background-op-one</tool-use-id>"
                        "<output-file>/test-data/tasks/bkdr7jbeo.output</output-file>"
                        "<status>completed</status>"
                        '<summary>Background command "Count 1 to 10" completed (exit code 0)</summary>'
                        "</task-notification>"
                    ),
                },
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.TRANSCRIPT_SOURCE,
            raw_event_id="background-completion",
        ),
    )

    assert not payloads(notification, actor_events.ActorAssignmentFinished)
    assert not payloads(notification, conversation_events.MessageCreated)
    located = payloads(notification, shell_events.ShellOutputLocated)
    finished = payloads(notification, shell_events.ShellOutputFinished)
    event_types = [type(canonical.payload) for canonical in notification.canonical_events]
    assert (
        len(located),
        located[0].payload.source_path,
        len(finished),
        finished[0].payload.shell_id,
        finished[0].payload.outcome,
        event_types.index(shell_events.ShellOutputLocated) < event_types.index(shell_events.ShellOutputFinished),
    ) == (
        1,
        "/test-data/tasks/bkdr7jbeo.output",
        1,
        domain_ids.ShellId(fixture.BACKGROUND_OP_ONE),
        fixture.SUCCEEDED,
        True,
    )


def test_claude_bg_completion_can_arrive_only() -> None:
    """Verify claude background completion can arrive only as a queue record."""
    completion = ClaudeCanonicalTranslator().translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.QUEUE_OPERATION_ID,
                fixture.OPERATION_FIELD: fixture.ENQUEUE,
                fixture.CONTENT_FIELD: (
                    "<task-notification><task-id>bkdr7jbeo</task-id>"
                    "<tool-use-id>background-op-one</tool-use-id>"
                    "<status>completed</status>"
                    '<summary>Background command "Count" completed (exit code 0)</summary>'
                    "</task-notification>"
                ),
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.TRANSCRIPT_SOURCE,
            raw_event_id="background-completion-queue",
        ),
    )

    finished = payloads(completion, shell_events.ShellOutputFinished)
    assert not payloads(completion, shell_events.ShellOutputLocated)
    assert len(finished) == 1
    finished_event = finished[0]
    assert finished_event.payload.shell_id == domain_ids.ShellId(fixture.BACKGROUND_OP_ONE)
    assert finished_event.payload.outcome == fixture.SUCCEEDED


def test_claude_queued_prompt_is_not_agent() -> None:
    """A user prompt can use the same queue as task notifications.

    Session 185232a0 showed a false Agent finished entry because the
    notification classifier used its agent default for an ordinary prompt.
    """
    queued = ClaudeCanonicalTranslator().translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.QUEUE_OPERATION_ID,
                fixture.OPERATION_FIELD: fixture.ENQUEUE,
                fixture.CONTENT_FIELD: "No create off fresh master",
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.TRANSCRIPT_SOURCE,
            raw_event_id="queued-user-prompt",
        ),
    )

    assert not payloads(queued, actor_events.ActorAssignmentFinished)
    assert queued.canonical_events == ()
    assert queued.decision == fixture.IGNORED_NONSEMANTIC


def test_claude_bg_completion_carries_jobs_own() -> None:
    """Verify claude background completion carries the jobs own outcome.

    The `<status>` is the JOB's, and the launch's says nothing about it: a
        command that exits non-zero launched perfectly. Values measured over every
        retained transcript (2026-08-18): completed, failed, killed, stopped.
    """
    assert claude_background_outcome(fixture.COMPLETED) == fixture.SUCCEEDED
    assert claude_background_outcome(fixture.FAILED) == fixture.FAILED
    assert claude_background_outcome("killed") == "cancelled"
    assert claude_background_outcome("stopped") == "cancelled"
    assert claude_background_outcome("something-new") == "unknown"


def test_claude_command_backgrounded_mid_run_says() -> None:
    """Verify claude command backgrounded mid run says so before it says finished.

    ctrl+b on a running command. The input never asked for the background and
        the response carries a task id anyway — and the ORDER matters: the
        `operation.finished` from this same delivery ends the follow of the file the
        command is still writing to unless the backgrounded fact lands first.
    """
    translation = ClaudeCanonicalTranslator().translate(
        raw_event(
            {
                fixture.HOOK_EVENT_NAME_FIELD: fixture.POST_TOOL_USE_HOOK,
                fixture.SESSION_ID_FIELD: fixture.SESSION_ONE_ID,
                fixture.TRANSCRIPT_PATH: "/test-data/session-one.jsonl",
                fixture.TOOL_NAME_FIELD: fixture.BASH_TOOL,
                fixture.TOOL_USE_ID_FIELD: "op-backgrounded",
                fixture.TOOL_INPUT_FIELD: {fixture.COMMAND_FIELD: "sleep 30; echo done"},
                fixture.TOOL_RESPONSE_FIELD: {fixture.BACKGROUND_TASK_ID_FIELD: "btk9y72c9"},
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.HOOK_SOURCE,
            raw_event_id="post-tool-use-backgrounded",
        ),
    )

    kinds = [type(canonical.payload).__name__ for canonical in translation.canonical_events]
    assert kinds.index("ShellBackgrounded") < kinds.index("ShellFinished")
    backgrounded = payloads(translation, shell_events.ShellBackgrounded)[0].payload
    assert backgrounded.shell_id == domain_ids.ShellId("op-backgrounded")
    assert backgrounded.shell_id == domain_ids.ShellId("op-backgrounded")


def test_claude_bg_launch_is_not_mid_run() -> None:
    """Verify claude background launch is not a mid run backgrounding.

    A command that ASKED for the background is already background at
        `operation.started`; announcing the transition too would be a second, later
        answer to a question the launch already settled.
    """
    translation = ClaudeCanonicalTranslator().translate(
        raw_event(
            {
                fixture.HOOK_EVENT_NAME_FIELD: fixture.POST_TOOL_USE_HOOK,
                fixture.SESSION_ID_FIELD: fixture.SESSION_ONE_ID,
                fixture.TRANSCRIPT_PATH: "/test-data/session-one.jsonl",
                fixture.TOOL_NAME_FIELD: fixture.BASH_TOOL,
                fixture.TOOL_USE_ID_FIELD: "op-native-background",
                fixture.TOOL_INPUT_FIELD: {
                    fixture.COMMAND_FIELD: "sleep 30",
                    fixture.RUN_IN_BACKGROUND_FIELD: True,
                },
                fixture.TOOL_RESPONSE_FIELD: {fixture.BACKGROUND_TASK_ID_FIELD: "btk9y72c9"},
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.HOOK_SOURCE,
            raw_event_id="post-tool-use-native-background",
        ),
    )

    assert not payloads(translation, shell_events.ShellBackgrounded)
