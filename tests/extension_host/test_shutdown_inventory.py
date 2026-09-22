# Copyright (c) 2026 Zhambyl Yermagambet
"""Ignore exited unrelated children, but still require every expected worker."""

from pathlib import Path
from unittest.mock import Mock, patch

import psutil
import pytest

from tests.extension_host import shutdown_process_fixture as fixtures


@pytest.mark.parametrize("failure", [psutil.NoSuchProcess(1), psutil.ZombieProcess(1)])
def test_exited_child_does_not_hide_live_worker(tmp_path: Path, failure: psutil.NoSuchProcess) -> None:
    """A process inventory can change before the command line is read."""
    lost = Mock(spec=psutil.Process)
    lost.cmdline.side_effect = failure
    worker = Mock(spec=psutil.Process)
    worker.cmdline.return_value = ["python", "-m", "baqylau_extension_api.runtime.worker", str(tmp_path / "package")]
    parent = Mock(spec=psutil.Process)
    parent.children.return_value = [lost, worker]
    with patch.object(psutil, "Process", return_value=parent):
        assert fixtures.worker_processes(tmp_path, expected=1) == (worker,)


def test_missing_expected_worker_still_fails(tmp_path: Path) -> None:
    """Skipping an exited child is not proof that the expected worker was running."""
    lost = Mock(spec=psutil.Process)
    lost.cmdline.side_effect = psutil.NoSuchProcess(1)
    parent = Mock(spec=psutil.Process)
    parent.children.return_value = [lost]
    with patch.object(psutil, "Process", return_value=parent), pytest.raises(AssertionError):
        fixtures.worker_processes(tmp_path, expected=1)
