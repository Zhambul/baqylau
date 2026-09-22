# Copyright (c) 2026 Zhambyl Yermagambet
"""Run extension projections after the core reaction drain."""

from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING

from audit.failures import FailureContext

PROJECTION_SCOPE_LIMIT = 100

if TYPE_CHECKING:
    from engine.worker import EngineWorker
    from extensions.processing_contract import ExtensionProcessingBatch


def project(engine_worker: EngineWorker, sources: ExtensionProcessingBatch | None) -> None:
    """Project new facts for every enabled projector package and scope."""
    projections = engine_worker.extension_services.projections
    if sources is None or projections is None:
        return
    floor = engine_worker.reaction_loop.dependencies.session_data_repository.progress()
    failure = partial(engine_worker.interpreter.failures.record, "extension projection", FailureContext())
    projections.run_selected(sources.packages, floor, PROJECTION_SCOPE_LIMIT, failure)
