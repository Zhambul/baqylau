# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep durable post-commit work separate from pure processing."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from baqylau_extension_api.models.observer_jobs import (
        ObservationCancelRequest,
        ObservationCancelResult,
        ObservationJobRequest,
        ObservationReconcileRequest,
    )
    from baqylau_extension_api.models.observer_results import ObservationJobResult


@runtime_checkable
class ExtensionObserver(Protocol):
    """Execute, cancel, or reconcile one host-accepted post-commit job."""

    def observe(self, observation_request: ObservationJobRequest) -> ObservationJobResult:
        """Handle one committed fact and propose new recorded observations."""

    def cancel_observation(self, cancel_request: ObservationCancelRequest) -> ObservationCancelResult:
        """Request a stop without claiming that the final outcome is known."""

    def reconcile_observation(self, reconcile_request: ObservationReconcileRequest) -> ObservationJobResult:
        """Inspect an uncertain effect without running the original work again."""
