# Copyright (c) 2026 Zhambyl Yermagambet
"""Durable extension command and observer job vocabulary."""

from enum import StrEnum


class JobKind(StrEnum):
    """Name the work that one durable extension job holds."""

    COMMAND = "command"
    OBSERVER = "observer"


class JobState(StrEnum):
    """Show the stored state of one durable extension job."""

    ACCEPTED = "accepted"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"
    OUTCOME_UNKNOWN = "outcome_unknown"


class JobCancelStatus(StrEnum):
    """Report a stop request without claiming a proven final state."""

    REQUESTED = "requested"
    CANCELED = "canceled"
    NOT_RUNNING = "not_running"
    OUTCOME_UNKNOWN = "outcome_unknown"
