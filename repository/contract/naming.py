"""Storage contract for idempotent automatic-title work."""

from __future__ import annotations

from typing import Protocol

from domain.naming import NamingJob


class NamingJobRepository(Protocol):
    def enqueue(self, naming_job: NamingJob) -> bool: ...

    def register_running(self, naming_job: NamingJob) -> tuple[NamingJob, bool]: ...

    def claim_next(self) -> NamingJob | None: ...

    def complete(self, key: str, title: str) -> None: ...

    def fail(self, key: str, reason: str) -> None: ...

    def find(self, key: str) -> NamingJob | None: ...
