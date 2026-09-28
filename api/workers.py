# Copyright (c) 2026 Zhambyl Yermagambet
"""Own the daemon work that runs beside the request loop."""

from __future__ import annotations

import threading
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING

from api import job_workers, worker_plans
from app import (
    provider_extension_runtime,
    provider_runtime as runtime_providers,
    provider_uploads as upload_providers,
)
from app.injection import Instances, resolve, seed
from extensions.manager_contract import ExtensionManager
from terminal.contract import TerminalPlugin

if TYPE_CHECKING:
    from collections.abc import Iterator


@dataclass
class _ManagedWorker:
    stop_event: threading.Event
    thread: threading.Thread
    plan: worker_plans.WorkerPlan

    @classmethod
    def start(cls, plan: worker_plans.WorkerPlan) -> _ManagedWorker:
        stop_event = threading.Event()
        thread = threading.Thread(
            target=plan.run,
            args=(stop_event,),
            daemon=True,
            name=plan.name,
        )
        thread.start()
        return cls(stop_event, thread, plan)

    def stop(self) -> None:
        self.stop_event.set()
        if self.plan.stop is not None:
            self.plan.stop()

    def join(self) -> None:
        self.thread.join(timeout=2)
        if self.thread.is_alive() and self.plan.requires_join:
            name = self.plan.name
            message = f"daemon worker did not stop: {name}"
            raise RuntimeError(message)


@dataclass
class _WorkerGroup:
    workers: tuple[_ManagedWorker, ...]
    model_terminal: TerminalPlugin
    terminal_plugin: TerminalPlugin
    extensions: ExtensionManager

    def start(self, plans: tuple[worker_plans.WorkerPlan, ...]) -> None:
        """Retain each started thread if a later thread fails to start."""
        try:
            for plan in plans:
                self.workers += (_ManagedWorker.start(plan),)
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        for worker in self.workers:
            worker.stop()
        # A naming call can wait on a native model process. Close its private
        # terminal first so the worker can observe cancellation.
        self.model_terminal.close()
        for worker in self.workers:
            worker.join()
        self.terminal_plugin.close()
        self.extensions.close()


def _start_workers(instances: Instances, extensions: ExtensionManager) -> _WorkerGroup:
    try:
        group, plans = _prepare_workers(instances, extensions)
    except BaseException:
        extensions.close()
        raise
    group.start(plans)
    return group


def _prepare_workers(
    instances: Instances, extensions: ExtensionManager,
) -> tuple[_WorkerGroup, tuple[worker_plans.WorkerPlan, ...]]:
    group = _WorkerGroup(
        (), resolve(instances, runtime_providers.model_terminal),
        resolve(instances, runtime_providers.terminal_plugin), extensions,
    )
    return group, worker_plans.plans(instances)


@contextmanager
def background_workers(instances: Instances) -> Iterator[None]:
    """Start daemon workers on entry and stop them on exit."""
    # Attachments are pruned from the row, not from directory timestamps.
    resolve(instances, upload_providers.uploads).prune()
    factory = resolve(instances, provider_extension_runtime.extension_manager_factory)
    extensions = factory.open_manager()
    with job_workers.job_executor(instances, extensions) as jobs:
        runtime = provider_extension_runtime.RuntimeManager(extensions)
        seed(instances, provider_extension_runtime.extension_runtime, runtime)
        jobs.submit_accepted(job_workers.RECOVERY_JOB_LIMIT)
        workers = _start_workers(instances, extensions)
        try:
            yield
        finally:
            workers.close()
