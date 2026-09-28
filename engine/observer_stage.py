# Copyright (c) 2026 Zhambyl Yermagambet
"""Run extension observers after the projection stage."""

from __future__ import annotations

from typing import TYPE_CHECKING

from engine.extension_health_stage import stage_health

OBSERVER_SCOPE_LIMIT = 100
SCHEDULE_LIMIT = 100

if TYPE_CHECKING:
    from engine.worker import EngineWorker
    from extensions.processing_contract import ExtensionProcessingBatch


def observe(engine_worker: EngineWorker, sources: ExtensionProcessingBatch | None) -> None:
    """Accept observer jobs for new facts, then schedule every accepted job."""
    observers = engine_worker.extension_services.observers
    if sources is None or observers is None:
        return
    observers.run_selected(sources.packages, OBSERVER_SCOPE_LIMIT, stage_health(engine_worker, "extension observer"))
    scheduler = engine_worker.extension_services.jobs
    if scheduler is not None:
        scheduler.submit_accepted(SCHEDULE_LIMIT)
