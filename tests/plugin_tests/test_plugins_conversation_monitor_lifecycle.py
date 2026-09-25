# Copyright (c) 2026 Zhambyl Yermagambet
"""Claude monitor lifecycle translation tests."""

from __future__ import annotations

from domain import (
    event_actor as actor_events,
    event_shell as shell_events,
    ids as domain_ids,
)
from harness.impl.claude_code.canonical.translator import ClaudeCanonicalTranslator
from tests.harness_names import CLAUDE_CODE_HARNESS
from tests.plugin_tests import vocabulary as fixture
from tests.plugin_tests.conversation_support import (
    translate_agent_notification,
)
from tests.plugin_tests.support_events import payloads, raw_event
from tests.plugin_tests.support_hooks import armed_monitor, monitor_notification
from tests.plugin_tests.support_values import text_of


def test_claude_repeated_monitor_arm_keeps_event() -> None:
    """Verify claude repeated monitor arm keeps the event ordinal."""
    translator = ClaudeCanonicalTranslator()
    armed_monitor(translator)
    first = translator.translate(
        monitor_notification(
            "tick-before-repeated-arm",
            '<task-id>bmfwjr03l</task-id><summary>Monitor event: "ticks"</summary><event>tick-1</event>',
        ),
    )
    armed_monitor(translator)
    second = translator.translate(
        monitor_notification(
            "tick-after-repeated-arm",
            '<task-id>bmfwjr03l</task-id><summary>Monitor event: "ticks"</summary><event>tick-2</event>',
        ),
    )

    first_event = payloads(first, shell_events.ShellProgressed)[0]
    second_event = payloads(second, shell_events.ShellProgressed)[0]
    assert first_event.payload.ordinal == 0
    assert second_event.payload.ordinal == 1
    assert first_event.event_id != second_event.event_id


def test_claude_monitor_event_for_unknown_task() -> None:
    """Verify claude monitor event for an unknown task is dropped not invented.

    The per-event notification names only the TASK id, so an event whose arm
        this translator never saw — a daemon restarted mid-watch — cannot be placed.
        Dropping it loses one line; inventing an operation would put a monitor on the
        tab that nothing ever armed.
    """
    translation = ClaudeCanonicalTranslator().translate(
        monitor_notification(
            "orphan-tick",
            '<task-id>never-seen</task-id><summary>Monitor event: "ticks"</summary><event>tick-1</event>',
        ),
    )

    assert translation.canonical_events == ()
    assert translation.decision == fixture.IGNORED_NONSEMANTIC


def test_claude_monitor_ends_on_its_own_notice() -> None:
    """Verify claude monitor ends on its own notification not on its arm.

    The arm's `operation.finished` arrives turns earlier and means only that
        the tool call returned — the projection ignores it for a monitor, which is
        why nothing ended one. The stream-ended notification is the monitor's own
        end, and it carries a tool_use_id, so it needs no memory of the arm.
    """
    translator = ClaudeCanonicalTranslator()
    armed_monitor(translator)

    ended = translator.translate(
        monitor_notification(
            "monitor-ended",
            "<task-id>bmfwjr03l</task-id>"
            "<tool-use-id>monitor-op-one</tool-use-id>"
            "<output-file>/tmp/tasks/bmfwjr03l.output</output-file>"
            "<status>completed</status>"
            '<summary>Monitor "ticks" stream ended</summary>',
        ),
    )

    assert not payloads(ended, actor_events.ActorAssignmentFinished)
    finished = payloads(ended, shell_events.ShellOutputFinished)
    assert len(finished) == 1
    finished_event = finished[0]
    assert finished_event.payload.shell_id == domain_ids.ShellId(fixture.MONITOR_OP_ONE)
    assert finished_event.payload.outcome == fixture.SUCCEEDED


def test_claude_monitor_stream_end_carries_its_last_event() -> None:
    """End the monitor when its stream-ended notice also carries the last event."""
    translator = ClaudeCanonicalTranslator()
    armed_monitor(translator)

    ended = translator.translate(
        monitor_notification(
            "monitor-stream-end",
            "<task-id>bmfwjr03l</task-id>"
            "<tool-use-id>monitor-op-one</tool-use-id>"
            "<output-file>/tmp/tasks/bmfwjr03l.output</output-file>"
            "<status>completed</status>"
            '<summary>Monitor "ticks" stream ended</summary>'
            "<event>backend test job success</event>",
        ),
    )

    finished = payloads(ended, shell_events.ShellOutputFinished)
    assert len(finished) == 1
    assert finished[0].payload.shell_id == domain_ids.ShellId(fixture.MONITOR_OP_ONE)
    # the last event rides the end notice; it is not a progress row of its own
    assert not payloads(ended, shell_events.ShellProgressed)


def test_claude_monitor_expiry_ends_the_armed_shell() -> None:
    """End a monitor whose expiry notice names only its task, not its shell."""
    translator = ClaudeCanonicalTranslator()
    armed_monitor(translator)

    ended = translator.translate(
        monitor_notification(
            "monitor-expiry",
            "<task-id>bmfwjr03l</task-id>"
            '<summary>Monitor event: "ticks"</summary>'
            "<event>[Monitor expired after 30m with 0 events delivered."
            " Re-arm it if you still need the watch.]</event>",
        ),
    )

    finished = payloads(ended, shell_events.ShellOutputFinished)
    assert len(finished) == 1
    assert finished[0].payload.shell_id == domain_ids.ShellId(fixture.MONITOR_OP_ONE)


def test_claude_monitor_expiry_without_its_arm_is_dropped() -> None:
    """Drop an expiry whose monitor this translator never saw."""
    translation = ClaudeCanonicalTranslator().translate(
        monitor_notification(
            "orphan-expiry",
            "<task-id>never-seen</task-id>"
            '<summary>Monitor event: "ticks"</summary>'
            "<event>[Monitor expired after 30m with 0 events delivered.]</event>",
        ),
    )

    assert translation.canonical_events == ()
    assert translation.decision == fixture.IGNORED_NONSEMANTIC


def test_claude_task_notices_are_counted_once() -> None:
    """The queue owns a monitor event and its later user copy is plumbing."""
    translator = ClaudeCanonicalTranslator()
    armed_monitor(translator)

    body = '<task-id>bmfwjr03l</task-id><summary>Monitor event: "ticks"</summary><event>tick-1</event>'
    enqueued = translator.translate(monitor_notification("enqueue-tick-1", body))
    delivered = translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.USER,
                fixture.UUID_FIELD: "delivered-tick-1",
                fixture.ORIGIN_FIELD: {fixture.KIND_FIELD: fixture.TASK_NOTIFICATION_ID},
                fixture.PROMPT_SOURCE_FIELD: fixture.SYSTEM,
                fixture.MESSAGE_FIELD: {fixture.CONTENT_FIELD: f"<task-notification>{body}</task-notification>"},
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.TRANSCRIPT_SOURCE,
            raw_event_id="delivered-tick-1",
        ),
    )

    progressed = payloads(enqueued, shell_events.ShellProgressed)
    assert len(progressed) == 1
    assert text_of(progressed[0].payload.content) == "tick-1"
    assert delivered.canonical_events == ()
    assert delivered.decision == fixture.IGNORED_NONSEMANTIC


def test_claude_absorbed_monitor_notices_end() -> None:
    """Claude Code 2.1.246 absorbs a monitor that ends mid-turn.

    The queue enqueues the event and end, then removes both with
    `absorbed_mid_turn` and writes task-notification attachments. No user record
    exists. The enqueue pair must therefore carry the two facts by itself.
    """
    translator = ClaudeCanonicalTranslator()
    armed_monitor(translator)

    end_body = (
        "<task-id>bmfwjr03l</task-id>"
        "<tool-use-id>monitor-op-one</tool-use-id>"
        "<output-file>/tmp/tasks/bmfwjr03l.output</output-file>"
        "<status>completed</status>"
        '<summary>Monitor "ticks" stream ended</summary>'
    )

    progressed = translator.translate(
        monitor_notification(
            "absorbed-event",
            '<task-id>bmfwjr03l</task-id><summary>Monitor event: "ticks"</summary><event>tick-1</event>',
        ),
    )
    ended = translator.translate(monitor_notification("absorbed-end", end_body))
    attachment = translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.ATTACHMENT,
                fixture.ATTACHMENT: {
                    fixture.TYPE_FIELD: "queued_command",
                    fixture.PROMPT_KIND: f"<task-notification>{end_body}</task-notification>",
                    "commandMode": fixture.TASK_NOTIFICATION_ID,
                    fixture.TIMESTAMP_FIELD: "2026-08-26T05:10:30.584Z",
                },
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.TRANSCRIPT_SOURCE,
            raw_event_id="absorbed-end-attachment",
        ),
    )

    assert text_of(payloads(progressed, shell_events.ShellProgressed)[0].payload.content) == "tick-1"
    assert len(payloads(ended, shell_events.ShellOutputFinished)) == 1
    assert payloads(ended, shell_events.ShellOutputFinished)[0].payload.shell_id == domain_ids.ShellId(
        fixture.MONITOR_OP_ONE,
    )
    assert attachment.canonical_events == ()
    assert attachment.decision == fixture.IGNORED_NONSEMANTIC


def test_claude_resumed_agent_keeps_later_queue() -> None:
    """Verify claude resumed agent keeps a later queue only result.

    An async agent may stop once, resume on its background notification,
        and stop again. Claude Code writes only the later report to the queue, so
        both revisions need distinct identities while duplicate copies converge.
    """
    translator = ClaudeCanonicalTranslator()

    waiting = payloads(
        translate_agent_notification(translator, "agent-waiting", "Waiting for background job"),
        actor_events.ActorAssignmentFinished,
    )[0]
    waiting_copy = payloads(
        translate_agent_notification(
            translator,
            "agent-waiting-copy",
            "Waiting for background job",
            fixture.USER,
        ),
        actor_events.ActorAssignmentFinished,
    )[0]
    completed = payloads(
        translate_agent_notification(translator, "agent-completed", "CHILD_JOB_DONE"),
        actor_events.ActorAssignmentFinished,
    )[0]

    assert waiting.event_id == waiting_copy.event_id
    assert completed.event_id != waiting.event_id
    assert text_of(completed.payload.result) == "CHILD_JOB_DONE"
