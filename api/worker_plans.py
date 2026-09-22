# Copyright (c) 2026 Zhambyl Yermagambet
"""Build the daemon worker plans."""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass

from api import worker_notifier
from app import provider_engine, provider_naming as naming_providers, provider_usage as usage_providers
from app.injection import Instances, resolve


@dataclass(frozen=True)
class WorkerPlan:
    """Define one named daemon worker."""

    name: str
    run: Callable[[threading.Event], None]
    stop: Callable[[], None] | None = None
    requires_join: bool = False


def plans(instances: Instances) -> tuple[WorkerPlan, ...]:
    """Build all daemon worker plans.

    Returns:
        The worker plans.

    """
    engine = resolve(instances, provider_engine.engine_worker)
    usage_state = resolve(instances, usage_providers.usage_state)
    naming_worker = resolve(instances, naming_providers.naming_worker)
    notifier = worker_notifier.notifier(instances)
    return (
        WorkerPlan("baqylau-engine", engine.run, engine.stop, requires_join=True),
        WorkerPlan("baqylau-usage", usage_state.run),
        WorkerPlan("baqylau-naming", naming_worker.run, naming_worker.stop),
        WorkerPlan("baqylau-notifier", notifier.run, notifier.stop),
    )
