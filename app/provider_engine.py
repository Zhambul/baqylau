# Copyright (c) 2026 Zhambyl Yermagambet
"""Assemble the engine worker and its native input watches."""

from typing import Annotated

from fastapi import Depends

from app import (
    provider_extension_executor,
    provider_extension_health,
    provider_extension_passes,
    provider_extension_runtime,
    provider_extension_sources,
    provider_interpreter,
    provider_reaction_loop,
    provider_runtime,
)
from app.injection import singleton
from app.provider_work_queue import EngineWork
from engine.extension_services import EngineExtensionServices
from engine.interpret.loop import Interpreter
from engine.react.loop import ReactionLoop
from engine.worker import EngineWorker


@singleton
def engine_extensions(
    runtime: provider_extension_runtime.Runtime,
    sources: provider_extension_sources.Processing,
    passes: provider_extension_passes.Passes,
    jobs: provider_extension_executor.JobSchedulerDep,
    health: provider_extension_health.Health,
) -> EngineExtensionServices:
    """Group the daemon's optional extension boundaries.

    Returns:
        Runtime publication, source processing, projection, observation, and job scheduling.

    """
    return EngineExtensionServices(
        runtime.manager, sources, passes.projections, passes.observers, jobs, passes.rebuilds, passes.histories,
        health,
    )


ExtensionServices = Annotated[EngineExtensionServices, Depends(engine_extensions)]


@singleton
def engine_worker(
    interpreter: Annotated[Interpreter, Depends(provider_interpreter.interpreter)],
    reaction_loop: Annotated[ReactionLoop, Depends(provider_reaction_loop.reaction_loop)],
    work_queue: EngineWork,
    runtime_configs: provider_runtime.RuntimeConfigs,
    extensions: ExtensionServices,
) -> EngineWorker:
    """Build the event-driven engine worker.

    Returns:
        The shared engine worker.

    """
    return EngineWorker(
        interpreter,
        reaction_loop,
        work_queue,
        tuple(entry.config.configuration_directory for entry in runtime_configs.entries()),
        extensions,
    )
