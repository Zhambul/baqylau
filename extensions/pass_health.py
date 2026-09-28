# Copyright (c) 2026 Zhambyl Yermagambet
"""Report each extension's failed and successful worker calls from a processing pass."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from extensions.models.extension_health import ExtensionHealth, HealthFailure, HealthPolicy, HealthState
from repository.contract.extension_health import ExtensionHealthStore


class PassHealth(Protocol):
    """Receive the outcome of one extension's work in a pass."""

    def failed(self, extension_id: str) -> None:
        """Report one failed call; the pass continues with other work."""
        ...

    def succeeded(self, extension_id: str) -> None:
        """Report one successful call."""
        ...


@dataclass
class HealthTracker:
    """Count failures durably; a success writes only for an extension that has failures."""

    store: ExtensionHealthStore
    clock: Callable[[], float]
    on_failed: Callable[[ExtensionHealth], None]
    policy: HealthPolicy = field(default_factory=HealthPolicy)
    _failing: set[str] | None = None

    def stage(self, where: str, audit: Callable[[str], None]) -> PassHealth:
        """Report for one engine stage, which also writes each failure to the audit.

        Returns:
            The reporter of the stage.

        """
        return _StageHealth(self, where, audit)

    def failed(self, extension_id: str, where: str) -> None:
        """Add one failure; at the limit, the failed callback runs once."""
        health = self.store.record_failure(HealthFailure(
            extension_id=extension_id, where=where, at=self.clock(), limit=self.policy.failure_limit,
        ))
        self._owners().add(extension_id)
        if health.state == HealthState.FAILED and health.consecutive_failures == self.policy.failure_limit:
            self.on_failed(health)

    def succeeded(self, extension_id: str) -> None:
        """Clear the failures of an extension that has them."""
        owners = self._owners()
        if extension_id in owners:
            self.store.record_success(extension_id, self.clock())
            owners.discard(extension_id)

    def _owners(self) -> set[str]:
        if self._failing is None:
            self._failing = {
                health.extension_id for health in self.store.read_health() if health.consecutive_failures > 0
            }
        return self._failing


@dataclass(frozen=True)
class AuditOnlyHealth(PassHealth):
    """Write failures to the audit only, for an engine that has no durable health."""

    audit: Callable[[str], None]

    def failed(self, extension_id: str) -> None:
        """Write one failure to the audit."""
        self.audit(extension_id)

    def succeeded(self, extension_id: str) -> None:
        """Keep no state for a success."""


@dataclass(frozen=True)
class _StageHealth(PassHealth):
    tracker: HealthTracker
    where: str
    audit: Callable[[str], None]

    def failed(self, extension_id: str) -> None:
        self.audit(extension_id)
        self.tracker.failed(extension_id, self.where)

    def succeeded(self, extension_id: str) -> None:
        self.tracker.succeeded(extension_id)
