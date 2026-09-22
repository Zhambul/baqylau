# Copyright (c) 2026 Zhambyl Yermagambet
"""Name the lifecycle states published by the HTTP boundary."""

from enum import StrEnum


class RuntimePhase(StrEnum):
    """Describe actual manager progress, not the requested enable state."""

    RUNNING = "running"
    PREPARING = "preparing"
    AWAITING_BOUNDARY = "awaiting_boundary"
    CLOSING = "closing"
    CLOSED = "closed"
    FENCED = "fenced"


class LifecycleKind(StrEnum):
    """Name user changes and internal recovery operations."""

    ENABLE = "enable"
    DISABLE = "disable"
    RELOAD = "reload"
    SETTINGS = "settings"
    RESTORE = "restore"
    FAILURE = "failure"


class AdmissionStatus(StrEnum):
    """Separate new work from the retained result of an exact retry."""

    ACCEPTED = "accepted"
    REPLAYED = "replayed"
