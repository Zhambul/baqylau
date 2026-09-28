# Copyright (c) 2026 Zhambyl Yermagambet
"""Provide the one scheduling slot that the daemon attaches to its job executor."""

from typing import Annotated

from fastapi import Depends

from app.injection import singleton
from extensions.job_scheduler_slot import JobSchedulerSlot
from extensions.job_scheduling_contract import JobScheduling
from extensions.lifecycle_control_contract import LifecycleUnavailableError


@singleton
def job_scheduler() -> JobSchedulerSlot:
    """Return the scheduling slot; only daemon startup attaches an executor.

    Returns:
        The shared slot, empty in request-only applications.

    """
    return JobSchedulerSlot()


JobSchedulerDep = Annotated[JobSchedulerSlot, Depends(job_scheduler)]


def job_scheduling(slot: JobSchedulerDep) -> JobScheduling:
    """Borrow the daemon-attached scheduler; never start an executor in a dependency.

    Returns:
        The attached scheduling slot.

    Raises:
        LifecycleUnavailableError: If no daemon executor is attached.

    """
    if not slot.attached:
        message = "extension job execution requires a running daemon"
        raise LifecycleUnavailableError(message)
    return slot


JobExecution = Annotated[JobScheduling, Depends(job_scheduling)]
