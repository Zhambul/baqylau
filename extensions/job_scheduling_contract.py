# Copyright (c) 2026 Zhambyl Yermagambet
"""Let the engine schedule stored accepted jobs without owning an executor."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from extensions.job_requests import JobKey


class JobScheduling(Protocol):
    """Schedule stored accepted jobs for background execution."""

    def submit(self, job_key: JobKey) -> None:
        """Schedule one accepted job unless it is already queued."""
        ...

    def submit_accepted(self, limit: int) -> int:
        """Schedule the oldest accepted jobs that are not already queued."""
        ...
