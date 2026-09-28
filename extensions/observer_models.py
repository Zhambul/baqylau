# Copyright (c) 2026 Zhambyl Yermagambet
"""Name the stores and the run context of one observer job."""

import time
from collections.abc import Callable
from dataclasses import dataclass

from extensions.control_policy import ExtensionControlPolicy
from extensions.observer_packages import ObserverPackage
from extensions.registry_package import RegistryPackage
from repository.contract.extension_jobs import ExtensionJobRepository
from repository.contract.extension_observers import ExtensionObserverRepository


@dataclass(frozen=True)
class ObserverRun:
    """Bind one observer package to the manager that committed its runtime."""

    package: ObserverPackage
    manager_id: str


@dataclass(frozen=True)
class ObserverJobContext:
    """Keep the borrowed packages, the checked manager, and the write policy for one run."""

    packages: tuple[RegistryPackage, ...]
    manager_id: str
    policy: ExtensionControlPolicy


@dataclass(frozen=True)
class ObserverStores:
    """Keep the job store, the observer store, and the settlement clock together."""

    observers: ExtensionObserverRepository
    jobs: ExtensionJobRepository
    clock: Callable[[], float] = time.time
