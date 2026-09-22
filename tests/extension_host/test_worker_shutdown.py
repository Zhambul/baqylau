# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep the extension manager owned until its engine consumer actually stops."""

from contextlib import ExitStack
from functools import partial
from threading import Event, Thread
from unittest.mock import Mock, patch

import pytest

from api.worker_plans import WorkerPlan
from extensions.manager_contract import ExtensionManager
from tests.extension_host import worker_shutdown_fixture as fixture


def test_engine_join_failure_retains_manager() -> None:
    """A timed-out engine join cannot be reported as safe extension shutdown."""
    released = Event()
    manager = Mock(spec=ExtensionManager)
    worker = fixture.held_worker(released)
    group = fixture.worker_group(manager, (worker,))
    with ExitStack() as cleanup:
        cleanup.callback(worker.thread.join, 5)
        cleanup.callback(released.set)
        with patch.object(worker.thread, "join"), pytest.raises(RuntimeError, match="held-engine"):
            group.close()
        manager.close.assert_not_called()
        released.set()
        group.close()
        manager.close.assert_called_once()


def test_partial_worker_start_is_cleaned() -> None:
    """A later thread-start failure stops the earlier engine and closes its manager."""
    manager = Mock(spec=ExtensionManager)
    group = fixture.worker_group(manager)
    plans = (
        WorkerPlan("first-engine", fixture.wait_for_stop, requires_join=True),
        WorkerPlan("failed-thread", fixture.wait_for_stop),
    )
    started: list[Thread] = []
    starting = partial(fixture.start_once, Thread.start, started)
    with (
        patch.object(Thread, "start", autospec=True, side_effect=starting),
        pytest.raises(RuntimeError, match="injected thread"),
    ):
        group.start(plans)
    assert len(started) == 1 and not started[0].is_alive()
    manager.close.assert_called_once()
