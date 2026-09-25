# Copyright (c) 2026 Zhambyl Yermagambet
"""Describe each extension's consecutive worker failures and its health state."""

from dataclasses import dataclass
from enum import StrEnum

from baqylau_extension_api.models.base import WireModel

DEFAULT_FAILURE_LIMIT = 5


class HealthState(StrEnum):
    """Name the health of one extension's worker calls."""

    HEALTHY = "healthy"
    FAILING = "failing"
    FAILED = "failed"


@dataclass(frozen=True)
class HealthPolicy:
    """Set how many consecutive failed calls make an extension failed."""

    failure_limit: int = DEFAULT_FAILURE_LIMIT


class ExtensionHealth(WireModel):
    """Keep the durable health of one extension; a successful call resets it."""

    extension_id: str
    state: HealthState
    consecutive_failures: int
    last_failure_where: str | None = None
    last_failure_at: float | None = None
    last_success_at: float | None = None


@dataclass(frozen=True)
class HealthFailure:
    """Name one failed worker call of one extension."""

    extension_id: str
    where: str
    at: float
    limit: int
