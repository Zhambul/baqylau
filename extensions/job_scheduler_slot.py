# Copyright (c) 2026 Zhambyl Yermagambet
"""Give early host services a scheduler that the daemon attaches after its executor opens."""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock
from typing import TYPE_CHECKING

from extensions.job_scheduling_contract import JobScheduling

if TYPE_CHECKING:
    from extensions.job_requests import JobKey


@dataclass
class JobSchedulerSlot(JobScheduling):
    """Forward scheduling to the attached executor; before that, jobs stay accepted.

    An accepted job that is not scheduled is not lost: the daemon schedules
    every accepted job when it attaches the executor, and each engine pass does
    so again.
    """

    scheduler: JobScheduling | None = None
    lock: Lock = field(default_factory=Lock)

    @property
    def attached(self) -> bool:
        """True after the daemon attached its executor."""
        with self.lock:
            return self.scheduler is not None

    def attach(self, scheduler: JobScheduling | None) -> None:
        """Attach the daemon executor, or detach it before the executor closes."""
        with self.lock:
            self.scheduler = scheduler

    def submit(self, job_key: JobKey) -> None:
        """Schedule one accepted job when an executor is attached."""
        with self.lock:
            scheduler = self.scheduler
        if scheduler is not None:
            scheduler.submit(job_key)

    def submit_accepted(self, limit: int) -> int:
        """Schedule the oldest accepted jobs when an executor is attached.

        Returns:
            The number of accepted jobs read, or zero with no executor.

        """
        with self.lock:
            scheduler = self.scheduler
        return 0 if scheduler is None else scheduler.submit_accepted(limit)
