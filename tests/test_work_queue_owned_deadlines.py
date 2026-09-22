# Copyright (c) 2026 Zhambyl Yermagambet
"""Check exact deadline replacement without real-time waits."""

from unittest.mock import Mock

import pytest

from core.work_queue import WorkKind, WorkQueue

SOURCE_KEY = "source"
LATER = 2
NONFINITE_DELAYS = (float("nan"), float("inf"), -float("inf"))


def test_owned_deadline_can_move_later(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replacing a deadline must not retain its earlier wake time."""
    clock = Mock(return_value=0)
    monkeypatch.setattr("core.work_queue.monotonic", clock)
    queue = WorkQueue()
    queue.set_deadline(WorkKind.EXTENSION_SOURCES, 1, SOURCE_KEY)
    queue.set_deadline(WorkKind.EXTENSION_SOURCES, LATER, SOURCE_KEY)
    clock.return_value = 1.0
    queue.put(WorkKind.RAW)
    assert queue.take() == {WorkKind.RAW}
    clock.return_value = LATER
    assert queue.take() == {WorkKind.EXTENSION_SOURCES}


def test_cancel_preserves_other_owned_deadlines(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cancel one source deadline without removing another producer's retry."""
    clock = Mock(return_value=0)
    monkeypatch.setattr("core.work_queue.monotonic", clock)
    queue = WorkQueue()
    queue.set_deadline(WorkKind.EXTENSION_SOURCES, 1, SOURCE_KEY)
    queue.schedule(WorkKind.EXTENSION_SOURCES, LATER, "retry")
    queue.set_deadline(WorkKind.EXTENSION_SOURCES, None, SOURCE_KEY)
    clock.return_value = 1.0
    queue.put(WorkKind.RAW)
    assert queue.take() == {WorkKind.RAW}
    clock.return_value = LATER
    assert queue.take() == {WorkKind.EXTENSION_SOURCES}


def test_cancel_does_not_remove_received_notice() -> None:
    """An explicit notice remains ready after its timer is cleared."""
    queue = WorkQueue()
    queue.put(WorkKind.EXTENSION_SOURCES)
    queue.set_deadline(WorkKind.EXTENSION_SOURCES, None, SOURCE_KEY)
    assert queue.take() == {WorkKind.EXTENSION_SOURCES}


@pytest.mark.parametrize("delay", [0, -1])
def test_due_deadline_is_ready(delay: float) -> None:
    """A due deadline requires no positive wait."""
    queue = WorkQueue()
    queue.set_deadline(WorkKind.EXTENSION_SOURCES, delay, SOURCE_KEY)
    assert queue.take() == {WorkKind.EXTENSION_SOURCES}


@pytest.mark.parametrize("delay", NONFINITE_DELAYS)
def test_nonfinite_deadline_is_rejected(delay: float) -> None:
    """Reject invalid delay values before they reach the native condition wait."""
    queue = WorkQueue()
    with pytest.raises(ValueError, match="finite"):
        queue.set_deadline(WorkKind.EXTENSION_SOURCES, delay, SOURCE_KEY)
