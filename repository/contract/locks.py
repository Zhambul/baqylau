"""Pid-liveness locks: who holds a named claim, and is it still alive."""

from __future__ import annotations

from typing import Protocol

from domain.locks import LockOutcome


class ProcessLockRepository(Protocol):
    def acquire(self, key: str, process_id: int) -> LockOutcome:
        """Take `key`. A holder whose process is gone is replaced (`stolen_stale`);
        a live holder denies, and the outcome names it."""
        ...

    def holder(self, key: str) -> int | None:
        """Read-only peek — used to report whether a daemon is already running."""
        ...

    def release(self, key: str, process_id: int) -> None:
        """Release only if still ours: a stolen claim must not be dropped by
        the process that lost it."""
        ...
