# Copyright (c) 2026 Zhambyl Yermagambet
"""A hook gets its reply before its raw events are written, and they are written in order.

A hook waited for its write, and the write waited for the engine's write lock,
so a Chrome approval waited for two such writes.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

from harness.hooks.recorder import HOOK_WRITE, OrderedHookRecorder
from tests import foundation_test_events
from tests.harness_names import CLAUDE_CODE_HARNESS
from tests.plugin_tests.support_events import raw_event

if TYPE_CHECKING:
    from collections.abc import Sequence

    from harness.models.raw_events import RawEvent

WAIT_SECONDS = 5
REUSED_ID = "reused"
FIRST = "first"
THIRD = "third"


class _HeldWriter:
    """Write only after the test releases it, and refuse a reused id."""

    def __init__(self) -> None:
        self.release = threading.Event()
        self.written: list[str] = []

    def record(self, raw_events: Sequence[RawEvent]) -> None:
        assert self.release.wait(WAIT_SECONDS)
        if any(event.raw_event_id == REUSED_ID for event in raw_events):
            message = "raw event id reused with different bytes"
            raise ValueError(message)
        self.written.extend(event.raw_event_id for event in raw_events)


def _delivery(raw_event_id: str) -> tuple[RawEvent, ...]:
    return (raw_event({}, harness=CLAUDE_CODE_HARNESS, source_type="hook", raw_event_id=raw_event_id),)


def _recorder() -> tuple[OrderedHookRecorder, _HeldWriter, foundation_test_events.RecordingAudit]:
    writer = _HeldWriter()
    audit = foundation_test_events.RecordingAudit()
    return OrderedHookRecorder(writer, audit), writer, audit


def test_hook_returns_before_its_write() -> None:
    """The delivery is queued while the writer waits, and it is written in arrival order."""
    recorder, writer, _audit = _recorder()
    for raw_event_id in (FIRST, "second", THIRD):
        recorder.record(_delivery(raw_event_id))
    assert not writer.written

    writer.release.set()
    recorder.flush()

    assert writer.written == [FIRST, "second", THIRD]
    recorder.close()


def test_one_bad_delivery_keeps_the_others() -> None:
    """A delivery that cannot be written is audited, and the deliveries around it are written."""
    recorder, writer, audit = _recorder()
    for raw_event_id in (FIRST, REUSED_ID, THIRD):
        recorder.record(_delivery(raw_event_id))

    writer.release.set()
    recorder.flush()

    assert writer.written == [FIRST, THIRD]
    assert [error[0] for error in audit.errors] == [HOOK_WRITE]
    recorder.close()


def test_close_writes_the_waiting_deliveries() -> None:
    """A daemon that stops writes the deliveries that it already answered."""
    recorder, writer, _audit = _recorder()
    recorder.record(_delivery("answered"))

    writer.release.set()
    recorder.close()

    assert writer.written == ["answered"]
