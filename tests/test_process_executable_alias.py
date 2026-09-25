# Copyright (c) 2026 Zhambyl Yermagambet
"""Check process identity when an installed command is a symlink."""

from pathlib import Path
from stat import S_IRWXU
from unittest.mock import Mock

import psutil
import pytest

from core import process

OBSERVED_PROCESS_ID = 123
CLI_NAME = "test-cli"


@pytest.mark.parametrize(
    ("status", "expected"),
    [(psutil.STATUS_RUNNING, True), (psutil.STATUS_ZOMBIE, False)],
)
def test_exited_process_is_not_live(status: str, monkeypatch: pytest.MonkeyPatch, *, expected: bool) -> None:
    """A process can exit before its parent collects the exit code."""
    observed = Mock(spec=psutil.Process, pid=OBSERVED_PROCESS_ID)
    observed.status.return_value = status
    observed.name.return_value = CLI_NAME
    observed.exe.return_value = "/bin/test-cli"
    observed.cmdline.return_value = [CLI_NAME]
    monkeypatch.setattr(psutil, "Process", lambda _pid: observed)
    assert process.process_alive(OBSERVED_PROCESS_ID, CLI_NAME) is expected
    assert process.process_is_alive(OBSERVED_PROCESS_ID) is expected


def test_alias_matches_only_its_executable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Do not confuse two versioned files that have the same name."""
    executable = tmp_path / "1.2.3"
    executable.touch()
    executable.chmod(S_IRWXU)
    alias = tmp_path / CLI_NAME
    alias.symlink_to(executable)
    monkeypatch.setenv("PATH", str(tmp_path))
    observed = Mock(spec=psutil.Process, pid=OBSERVED_PROCESS_ID)
    observed.name.return_value = executable.name
    observed.exe.return_value = str(executable)
    observed.cmdline.return_value = [str(executable)]
    monkeypatch.setattr(psutil, "Process", lambda _pid: observed)
    assert process.process_alive(OBSERVED_PROCESS_ID, CLI_NAME)
    observed.exe.return_value = str(tmp_path / "other" / "1.2.3")
    assert not process.process_alive(OBSERVED_PROCESS_ID, CLI_NAME)


def test_update_sibling_matches_the_installed_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep matching a running CLI after the installed name moves to a newer version."""
    old_version = tmp_path / "1.2.3"
    new_version = tmp_path / "1.2.4"
    old_version.touch()
    new_version.touch()
    old_version.chmod(S_IRWXU)
    new_version.chmod(S_IRWXU)
    alias = tmp_path / CLI_NAME
    alias.symlink_to(new_version)
    monkeypatch.setenv("PATH", str(tmp_path))
    observed = Mock(spec=psutil.Process, pid=OBSERVED_PROCESS_ID)
    observed.name.return_value = old_version.name
    observed.exe.return_value = str(old_version)
    observed.cmdline.return_value = [str(old_version)]
    monkeypatch.setattr(psutil, "Process", lambda _pid: observed)
    assert process.process_alive(OBSERVED_PROCESS_ID, CLI_NAME)
