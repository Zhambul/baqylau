# Copyright (c) 2026 Zhambyl Yermagambet
"""Accept exited-only groups without hiding real process permission failures."""

import os
import signal
import sys
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import psutil
import pytest

from extensions.preparation_runner import BoundedPreparationRunner
from extensions.process_groups import kill_process_group
from tests.extension_host import preparation_fixture

OWNED_GROUP = 12345
ROOT = Path(__file__).resolve().parents[2]


def test_cleanup_accepts_real_zombie_group(tmp_path: Path) -> None:
    """A separate process preserves a real zombie until the host cleanup check."""
    command = preparation_fixture.command(tmp_path, "pass")
    arguments = (sys.executable, "-m", "tests.extension_host.zombie_probe")
    selected = replace(command, arguments=arguments, directory=ROOT)
    output = BoundedPreparationRunner().run_preparation(selected)
    assert output.return_code == 0, output.stderr.decode()


def test_cleanup_keeps_live_permission_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A process with changed credentials must not be treated as already stopped."""
    _denied_group(monkeypatch, psutil.STATUS_RUNNING)
    with pytest.raises(PermissionError, match="denied"):
        kill_process_group(OWNED_GROUP)


def test_cleanup_accepts_exited_members(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only zombie membership is sufficient to accept the XNU error."""
    _denied_group(monkeypatch, psutil.STATUS_ZOMBIE)
    kill_process_group(OWNED_GROUP)


@pytest.mark.parametrize("group", [0, -1])
def test_cleanup_rejects_broad_group_targets(group: int, monkeypatch: pytest.MonkeyPatch) -> None:
    """Cleanup cannot select the caller's group or broadcast to unrelated processes."""
    selected = Mock()
    monkeypatch.setattr(os, "killpg", selected)
    with pytest.raises(ValueError, match="positive group"):
        kill_process_group(group)
    selected.assert_not_called()


def _denied_group(monkeypatch: pytest.MonkeyPatch, status: str) -> None:
    selected = Mock(pid=OWNED_GROUP)
    selected.status.return_value = status
    monkeypatch.setattr(psutil, "process_iter", lambda: iter((selected,)))
    monkeypatch.setattr(os, "getpgid", lambda _pid: OWNED_GROUP)
    monkeypatch.setattr(os, "killpg", _denied_signal)


def _denied_signal(pid: int, selected_signal: int) -> None:
    assert pid == OWNED_GROUP and selected_signal == signal.SIGKILL
    message = "denied"
    raise PermissionError(message)
