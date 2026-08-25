"""Coalesce a repeated loop failure before it reaches durable audit."""

from __future__ import annotations

import sys
import time
from collections.abc import Callable
from dataclasses import dataclass

from pydantic import JsonValue

from audit.recorder import AuditRecorder

REPEAT_REPORT_SECONDS = 60.0


@dataclass
class FailureState:
    fingerprint: tuple[str, str]
    reported_at: float
    suppressed_repeats: int = 0


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
        self._states: dict[tuple[str, tuple[tuple[str, str], ...]], FailureState] = {}

    def record(self, where: str, context: dict[str, JsonValue]) -> None:
        """Record a new failure shape or a counted periodic repeat."""
        error = sys.exception()
        fingerprint = (
            type(error).__name__ if error is not None else "unknown",
            str(error) if error is not None else "",
        )
        location = (
            where,
            tuple(sorted((name, repr(value)) for name, value in context.items())),
        )
        now = self.clock()
        state = self._states.get(location)
        if (
            state is not None
            and state.fingerprint == fingerprint
            and now - state.reported_at < self.repeat_report_seconds
        ):
            state.suppressed_repeats += 1
            return
        report_context = dict(context)
        if state is not None and state.suppressed_repeats:
            report_context["suppressed_repeats"] = state.suppressed_repeats
        try:
            self.audit.error(
                str(context.get("session_id", "")),
                f"{self.owner} ({where})",
                report_context,
            )
        except Exception:
            return
        self._states[location] = FailureState(fingerprint, now)
