# Copyright (c) 2026 Zhambyl Yermagambet
"""Claude monitor stream-end and expiry translation tests."""

from __future__ import annotations

from domain import (
    event_shell as shell_events,
    ids as domain_ids,
)
from harness.impl.claude_code.canonical.translator import ClaudeCanonicalTranslator
from tests.plugin_tests import vocabulary as fixture
from tests.plugin_tests.support_events import payloads
from tests.plugin_tests.support_hooks import armed_monitor, monitor_notification
from tests.plugin_tests.support_values import text_of


def test_monitor_stream_end_carries_last_event() -> None:
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
    # the last event rides the end notice, and it stays the monitor's latest status
    progressed = payloads(ended, shell_events.ShellProgressed)
    assert [text_of(event.payload.content) for event in progressed] == ["backend test job success"]


def test_monitor_expiry_ends_the_armed_shell() -> None:
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


def test_monitor_expiry_without_arm_is_dropped() -> None:
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
