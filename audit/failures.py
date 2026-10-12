# Copyright (c) 2026 Zhambyl Yermagambet
"""Coalesce a repeated loop failure before it reaches durable audit."""

from __future__ import annotations

import sys
import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from audit.documents import AuditDocument
from domain.ids import CanonicalEventId, SessionId

if TYPE_CHECKING:
    from collections.abc import Callable

    from audit.documents import AuditContent

REPEAT_REPORT_SECONDS = 60.0


class ErrorRecorder(Protocol):
    """Record an operational error."""

    def error(
        self,
        session_or_log: str = "",
        func: str = "",
        context: AuditContent = None,
    ) -> None:
        """Record the active exception."""
        ...


@dataclass
class FailureState:
    """Keep the last report state for one failure location."""

    fingerprint: tuple[str, str]
    reported_at: float
    suppressed_repeats: int = 0


class FailureContext(AuditDocument):
    """Describe the event processing context for one failure."""

    session_id: SessionId = SessionId("")
    source_identity: str | None = None
    source: str | None = None
    event_id: CanonicalEventId | None = None
    cursor: int | None = None
    entry_id: CanonicalEventId | None = None
    entry_type: str | None = None
    event_type: str | None = None
    suppressed_repeats: int | None = None

    def with_suppressed_repeats(self, count: int) -> FailureContext:
        """Return this context with the repeat count.

        Returns:
            This context with the repeat count.

        """
        return FailureContext(
            session_id=self.session_id,
            source_identity=self.source_identity,
            source=self.source,
            event_id=self.event_id,
            cursor=self.cursor,
            entry_id=self.entry_id,
            entry_type=self.entry_type,
            event_type=self.event_type,
            suppressed_repeats=count,
        )


class CoalescingFailureRecorder:
    """Write the first failure and one counted update per time interval."""

    def __init__(
        self,
        error_recorder: ErrorRecorder,
        owner: str,
        clock: Callable[[], float] = time.monotonic,
        repeat_report_seconds: float = REPEAT_REPORT_SECONDS,
    ) -> None:
        """Create a recorder with an audit target and a repeat interval."""
        self.audit = error_recorder
        self.owner = owner
        self.clock = clock
        self.repeat_report_seconds = repeat_report_seconds
        self._states: dict[tuple[str, str], FailureState] = {}
        self._lock = threading.Lock()

    def record(self, where: str, failure_context: FailureContext) -> None:
        """Record a new failure shape or a counted periodic repeat, from any thread."""
        with self._lock:
            self._record(where, failure_context)

    def _record(self, where: str, failure_context: FailureContext) -> None:
        fingerprint = _failure_fingerprint()
        location = (where, failure_context.model_dump_json())
        now = self.clock()
        state = self._states.get(location)
        if state and state.fingerprint == fingerprint and now - state.reported_at < self.repeat_report_seconds:
            state.suppressed_repeats += 1
            return
        report_context = failure_context
        if state and state.suppressed_repeats:
            report_context = failure_context.with_suppressed_repeats(state.suppressed_repeats)
        self.audit.error(
            failure_context.session_id,
            f"{self.owner} ({where})",
            report_context,
        )
        self._states[location] = FailureState(fingerprint, now)


def _failure_fingerprint() -> tuple[str, str]:
    """Return the type and message of the active exception.

    Returns:
        Type and message of the active exception.

    """
    error = sys.exception()
    if error is None:
        return "unknown", ""
    return type(error).__name__, str(error)
