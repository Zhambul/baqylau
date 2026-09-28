# Copyright (c) 2026 Zhambyl Yermagambet
"""Run extension projections after the core reaction drain."""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.work_queue import WorkKind
from engine.extension_health_stage import stage_health
from extensions.core_projection_transforms import core_transforms

PROJECTION_SCOPE_LIMIT = 100
REBUILD_CONTINUATION_SECONDS = 0.01
REBUILD_CONTINUATION_KEY = "projection rebuild continuation"

if TYPE_CHECKING:
    from engine.sessiondata.contract import CoreChangeTransform
    from engine.worker import EngineWorker
    from extensions.processing_contract import ExtensionProcessingBatch


def project(engine_worker: EngineWorker, sources: ExtensionProcessingBatch | None) -> None:
    """Project new facts for every enabled projector package and its pending scopes."""
    projections = engine_worker.extension_services.projections
    if sources is None or projections is None:
        return
    health = stage_health(engine_worker, "extension projection")
    projections.run_selected(sources.packages, PROJECTION_SCOPE_LIMIT, health)


def core_transform(
    engine_worker: EngineWorker, sources: ExtensionProcessingBatch | None,
) -> CoreChangeTransform | None:
    """Build the core row transform of this batch's enabled projection transforms.

    Returns:
        The transform, or None with no batch, no projection pass, or no transform package.

    """
    projections = engine_worker.extension_services.projections
    if sources is None or projections is None:
        return None
    return core_transforms(sources.packages, stage_health(engine_worker, "extension core transform"))


def rebuild(engine_worker: EngineWorker, sources: ExtensionProcessingBatch | None) -> None:
    """Advance building projection generations by one bounded pass, and continue while work remains."""
    rebuilds = engine_worker.extension_services.projection_rebuild
    if sources is None or rebuilds is None:
        return
    read = rebuilds.build_pending(sources.packages, stage_health(engine_worker, "extension projection rebuild"))
    engine_worker.work_queue.set_deadline(
        WorkKind.CANONICAL, REBUILD_CONTINUATION_SECONDS if read else None, REBUILD_CONTINUATION_KEY,
    )
