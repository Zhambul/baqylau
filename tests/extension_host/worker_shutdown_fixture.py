# Copyright (c) 2026 Zhambyl Yermagambet
"""Supply controlled thread starts and stop conditions for resource-order tests."""

from collections.abc import Callable
from functools import partial
from threading import Event, Thread
from unittest.mock import Mock

from api import workers
from api.worker_plans import WorkerPlan
from extensions.manager_contract import ExtensionManager
from terminal.contract import TerminalPlugin


def worker_group(
    manager: ExtensionManager, selected: tuple[workers._ManagedWorker, ...] = (),
) -> workers._WorkerGroup:
    """Keep terminal doubles separate from the real thread ownership logic.

    Returns:
        A real worker group with only the terminal boundary replaced.

    """
    terminal = Mock(spec=TerminalPlugin)
    model_terminal = Mock(spec=TerminalPlugin)
    return workers._WorkerGroup(selected, model_terminal, terminal, manager)  # noqa: SLF001 -- White-box ownership test.


def held_worker(released: Event) -> workers._ManagedWorker:
    """Create an actual engine thread with a controlled release condition.

    Returns:
        The private thread owner under direct resource-order test.

    """
    plan = WorkerPlan("held-engine", partial(wait_for_release, released), requires_join=True)
    return workers._ManagedWorker.start(plan)  # noqa: SLF001 -- White-box ownership test.


def wait_for_stop(stop: Event) -> None:
    """Leave when the production stop event is set."""
    stop.wait()


def wait_for_release(released: Event, _stop: Event) -> None:
    """Keep a held engine alive until the test releases it."""
    released.wait()


def start_once(original: Callable[[Thread], None], started: list[Thread], thread: Thread) -> None:
    """Fail a later start after the first actual thread is running.

    Raises:
        RuntimeError: When the test reaches the second thread start.

    """
    if started:
        message = "injected thread start failure"
        raise RuntimeError(message)
    started.append(thread)
    original(thread)
