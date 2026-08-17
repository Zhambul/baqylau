"""Row DTO to a lock outcome."""

from __future__ import annotations

from domain.locks import LockOutcome


def denied(holder_process_id: int) -> LockOutcome:
    return LockOutcome("denied", holder_process_id)


def claimed() -> LockOutcome:
    return LockOutcome("claimed")


def stolen_stale(previous_process_id: int) -> LockOutcome:
    return LockOutcome("stolen_stale", previous_process_id)


def unavailable() -> LockOutcome:
    """The lock database could not be reached at all — never a silent success."""
    return LockOutcome("unavailable")
