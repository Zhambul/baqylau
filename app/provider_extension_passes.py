# Copyright (c) 2026 Zhambyl Yermagambet
"""Provide the observer pass and group the engine's extension passes."""

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends

from app import (
    provider_extension_jobs as job_providers,
    provider_fact_storage as fact_providers,
    provider_projections,
    provider_reprocessing,
)
from app.injection import singleton
from extensions.observer_pass import ObserverPass
from extensions.projection_pass import ProjectionPass
from extensions.projection_rebuild import ProjectionRebuild
from repository.contract.extension_observers import ExtensionObserverRepository
from repository.contract.history_reprocessing import HistoryStores
from repository.contract.interpretations import InterpretationRepository


@singleton
def observer_pass(
    facts: Annotated[InterpretationRepository, Depends(fact_providers.interpretations)],
    observers: Annotated[ExtensionObserverRepository, Depends(job_providers.extension_observers)],
    jobs: job_providers.Jobs,
) -> ObserverPass:
    """Return the extension observer pass.

    Returns:
        The observer pass over the shared fact reader and the observer store.

    """
    return ObserverPass(facts=facts, observers=observers, jobs=jobs)


Observers = Annotated[
    ObserverPass,
    Depends(observer_pass),
]


@dataclass(frozen=True)
class ExtensionPassSet:
    """Group the engine's projection, rebuild, history, and observer passes."""

    projections: ProjectionPass
    rebuilds: ProjectionRebuild
    observers: ObserverPass
    histories: HistoryStores


def extension_passes(
    projections: provider_projections.Projections,
    rebuilds: provider_reprocessing.Rebuilds,
    observers: Observers,
    histories: provider_reprocessing.HistoryStoresDep,
) -> ExtensionPassSet:
    """Group the engine's extension passes.

    Returns:
        The projection, rebuild, history, and observer passes.

    """
    return ExtensionPassSet(projections=projections, rebuilds=rebuilds, observers=observers, histories=histories)


Passes = Annotated[ExtensionPassSet, Depends(extension_passes)]
