"""Coalesce a repeated loop failure before it reaches durable audit."""

from __future__ import annotations

import sys
import time
from collections.abc import Callable
from dataclasses import dataclass

from audit.models import AuditDocument
from audit.recorder import AuditRecorder
from domain.ids import CanonicalEventId, SessionId

REPEAT_REPORT_SECONDS = 60.0


@dataclass
class FailureState:
    fingerprint: tuple[str, str]
    reported_at: float
    suppressed_repeats: int = 0


class FailureContext(AuditDocument):
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
        audit_recorder: AuditRecorder,
        owner: str,
        clock: Callable[[], float] = time.monotonic,
        repeat_report_seconds: float = REPEAT_REPORT_SECONDS,
    ) -> None:
        self.audit = audit_recorder
        self.owner = owner
        self.clock = clock
        self.repeat_report_seconds = repeat_report_seconds
        self._states: dict[tuple[str, str], FailureState] = {}

    def record(self, where: str, failure_context: FailureContext) -> None:
        """Record a new failure shape or a counted periodic repeat."""
        error = sys.exception()
        fingerprint = (
            type(error).__name__ if error is not None else "unknown",
            str(error) if error is not None else "",
        )
        location = (where, failure_context.model_dump_json())
        now = self.clock()
        state = self._states.get(location)
        if (
            state is not None
            and state.fingerprint == fingerprint
            and now - state.reported_at < self.repeat_report_seconds
        ):
            state.suppressed_repeats += 1
            return
        report_context = failure_context
        if state is not None and state.suppressed_repeats:
            report_context = failure_context.with_suppressed_repeats(state.suppressed_repeats)
        try:
            self.audit.error(
                failure_context.session_id,
                f"{self.owner} ({where})",
                report_context,
            )
        except Exception:
            return
        self._states[location] = FailureState(fingerprint, now)
