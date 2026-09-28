# Copyright (c) 2026 Zhambyl Yermagambet
"""Group the optional extension services of the engine worker."""

from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass

from extensions.job_scheduling_contract import JobScheduling
from extensions.manager_contract import ExtensionRuntimeBoundary
from extensions.observer_pass import ObserverPass
from extensions.pass_health import HealthTracker
from extensions.processing_contract import ExtensionProcessing, ExtensionProcessingBatch
from extensions.projection_pass import ProjectionPass
from extensions.projection_rebuild import ProjectionRebuild
from repository.contract.history_reprocessing import HistoryStores


@dataclass(frozen=True)
class EngineExtensionServices(ExtensionProcessing):
    """Group optional runtime publication and source processing for standalone engine consumers."""

    runtime: ExtensionRuntimeBoundary | None = None
    processing: ExtensionProcessing | None = None
    projections: ProjectionPass | None = None
    observers: ObserverPass | None = None
    jobs: JobScheduling | None = None
    projection_rebuild: ProjectionRebuild | None = None
    histories: HistoryStores | None = None
    health: HealthTracker | None = None

    def capture_batch(self) -> AbstractContextManager[ExtensionProcessingBatch | None]:
        """Keep the normal core-only engine path free from dummy extension services.

        Returns:
            The real retained runtime context, or an explicit absent source batch.

        """
        return nullcontext(None) if self.processing is None else self.processing.capture_batch()
